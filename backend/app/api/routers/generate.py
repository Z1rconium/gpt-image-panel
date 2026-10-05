import asyncio
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from ...runtime.state import state
from ...services.job_events import (
    publish_job_edges,
    get_cached_generate_job_previews,
    get_job_subscribers,
    get_jobs_subscribers,
    publish_queue,
    reconcile_active_generate_jobs,
    resolve_generate_job_view,
    serialize_sse_event,
)
from ...runtime.blocking import run_db_operation, run_image_operation
from ...services.job_cancel import cancel_image_job
from ...services.job_queue import queue_image_job
from ...services.poll_backoff import next_poll_delay
from ...repositories.sse_limiter import sse_limiter
from ...core import security as auth
from ...core import settings as config
from ...core.api_paths import normalize_api_path
from ...core.constants import ACTIVE_GENERATE_JOB_STATUSES, ERROR_GENERATE_JOB_STATUSES
from ...core.media import MASK_CONTENT_TYPE, safe_image_path, safe_mask_path
from ...repositories.gallery.queries import get_gallery_entry
from ...core.observability import metrics
from ...repositories.image_jobs import (
    aggregate_image_job_units,
    clear_generate_job_history as clear_persisted_generate_job_history,
    get_generate_job as get_persisted_generate_job,
    get_generate_sse_edges,
    list_generate_jobs as list_persisted_generate_jobs,
)
from ...schemas.common import MessageResponse
from ...schemas.generation import (
    GenerateJobResponse,
    GenerateJobStatus,
    GenerateRequest,
    JobDiagnosticsResponse,
)


router = APIRouter()
logger = logging.getLogger(__name__)
SSE_IDLE_CHECK_SECONDS = 1.0
SSE_QUEUE_MAXSIZE = 20
MAX_DIAGNOSTIC_UNITS = 20
MAX_DIAGNOSTIC_TASK_ID_CHARS = 64


def _unit_diagnostics_entry(unit: dict) -> dict:
    """Public, bounded per-unit diagnostics view (no raw follow-up URLs)."""
    remote = unit.get("remote") if isinstance(unit.get("remote"), dict) else None
    summary = None
    if remote:
        task_id = remote.get("task_id")
        summary = {
            "phase": str(remote.get("phase") or ""),
            "task_id": str(task_id)[:MAX_DIAGNOSTIC_TASK_ID_CHARS] if task_id else None,
            "submitted_at": remote.get("submitted_at"),
            "deadline_at": remote.get("deadline_at"),
            "has_status_url": bool(remote.get("status_url")),
            "has_result_url": bool(remote.get("result_url")),
            "has_cancel_url": bool(remote.get("cancel_url")),
            "has_idempotency_key": bool(remote.get("idempotency_key")),
        }
    return {
        "unit_id": str(unit.get("unit_id") or ""),
        "unit_index": int(unit.get("unit_index") or 0),
        "status": str(unit.get("status") or ""),
        "stage": unit.get("stage"),
        "message": unit.get("message"),
        "error": unit.get("error"),
        "attempts": int(unit.get("attempts") or 0),
        "recovery_count": int(unit.get("recovery_count") or 0),
        "remote": summary,
        "diagnostics": unit.get("diagnostics"),
    }


def json_payload_key(payload: dict | list) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _start_generate_sse_poller() -> None:
    task = getattr(state, "generate_jobs_sse_poller_task", None)
    if task and not task.done():
        return
    state.generate_jobs_sse_poller_task = asyncio.create_task(_poll_generate_sse())


async def _poll_generate_sse() -> None:
    """Push generation job changes to every generate SSE subscriber.

    A single loop serves both the jobs-list feed and the per-job feeds, reading
    both change signals in one DB operation on one connection. Job updates are
    already published in-process at the write site, so this poll only covers
    writes from other processes and backs off while nothing changes.
    """
    last_list_edge: tuple[int, str] | None = None
    last_job_edges: dict[str, str] = {}
    delay = config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS
    try:
        while True:
            list_subscribers = list(get_jobs_subscribers())
            subscribers_by_job = {
                job_id: list(subscribers)
                for job_id, subscribers in get_job_subscribers().items()
                if subscribers
            }
            if not list_subscribers and not subscribers_by_job:
                break

            list_edge, job_edges = await run_db_operation(
                get_generate_sse_edges,
                list_statuses=ACTIVE_GENERATE_JOB_STATUSES,
                job_ids=set(subscribers_by_job) if subscribers_by_job else None,
                include_list_edge=bool(list_subscribers),
                metric_name="poll_generate_sse_edges",
            )
            metrics.increment("sse.poll_queries.generate")
            changed = False

            if list_subscribers and list_edge != last_list_edge:
                last_list_edge = list_edge
                jobs = await run_db_operation(
                    list_persisted_generate_jobs,
                    statuses=ACTIVE_GENERATE_JOB_STATUSES,
                    metric_name="poll_generate_jobs",
                )
                jobs = reconcile_active_generate_jobs(jobs)
                event = {"event": "jobs", "data": jobs}
                for queue in list_subscribers:
                    publish_queue(queue, event)
                changed = True

            async def read_event(job_id: str) -> dict:
                job = await run_db_operation(
                    get_persisted_generate_job,
                    job_id,
                    metric_name="poll_generate_job",
                )
                return (
                    {"event": "job", "data": job}
                    if job
                    else {"event": "_missing", "data": None}
                )

            changed = (
                await publish_job_edges(
                    subscribers_by_job=subscribers_by_job,
                    edges=job_edges,
                    last_edges=last_job_edges,
                    read_event=read_event,
                )
                or changed
            )

            base_interval = config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS
            delay = next_poll_delay(
                base_interval=base_interval,
                current_delay=delay,
                changed=changed,
                max_backoff_seconds=config.SSE_IDLE_BACKOFF_MAX_SECONDS,
            )
            if delay > base_interval:
                metrics.increment("sse.poll_idle_backoff")
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Generate SSE poller stopped after error", exc_info=True)
    finally:
        if (
            getattr(state, "generate_jobs_sse_poller_task", None)
            is asyncio.current_task()
        ):
            state.generate_jobs_sse_poller_task = None


@router.post("/api/generate", response_model=GenerateJobResponse, status_code=202)
async def generate(req: GenerateRequest):
    return await queue_image_job(
        req=req,
        operation="generation",
        api_path=lambda preset: normalize_api_path(
            req.api_path or str(preset.get("api_path") or "/v1/images/generations")
        ),
        queued_message="Queued image generation",
    )


@router.get("/api/generate/jobs", response_model=list[GenerateJobStatus])
async def list_generate_jobs(
    include_finished: bool = Query(default=False),
    failed_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    before_updated_at: str | None = Query(default=None, max_length=64),
    before_job_id: str | None = Query(default=None, max_length=128),
):
    if include_finished:
        statuses = ERROR_GENERATE_JOB_STATUSES if failed_only else None
        jobs = await run_db_operation(
            list_persisted_generate_jobs,
            statuses=statuses,
            limit=limit,
            offset=offset,
            before_updated_at=before_updated_at,
            before_job_id=before_job_id,
            metric_name="list_generate_jobs",
        )
    else:
        jobs = await run_db_operation(
            list_persisted_generate_jobs,
            statuses=ACTIVE_GENERATE_JOB_STATUSES,
            metric_name="list_active_generate_jobs",
        )
    return [GenerateJobStatus(**job) for job in jobs]


@router.get("/api/generate/jobs/events")
async def stream_generate_jobs(request: Request):
    client_ip = auth.get_client_ip(request)
    sse_lease = await sse_limiter.acquire(client_ip)
    if not sse_lease:
        raise HTTPException(status_code=429, detail="Too many SSE connections")

    async def event_stream():
        start = time.monotonic()
        last_refresh_at = start
        last_payload = None
        last_sent = 0.0
        queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
        subscribers = get_jobs_subscribers()
        subscribers.add(queue)
        _start_generate_sse_poller()
        try:
            jobs = await run_db_operation(
                list_persisted_generate_jobs,
                statuses=ACTIVE_GENERATE_JOB_STATUSES,
                metric_name="stream_generate_jobs_initial",
            )
            jobs = reconcile_active_generate_jobs(jobs)
            last_payload = json_payload_key(jobs)
            last_sent = time.monotonic()
            yield serialize_sse_event("jobs", jobs)

            while True:
                if await request.is_disconnected():
                    break
                now = time.monotonic()
                refreshed_at = await sse_limiter.refresh_if_needed(
                    sse_lease,
                    last_refresh_at,
                )
                if refreshed_at is None:
                    break
                last_refresh_at = refreshed_at
                if now - start > config.SSE_CONNECTION_TTL_SECONDS:
                    break
                if now - last_sent >= 15:
                    last_sent = now
                    yield ": keep-alive\n\n"
                    continue
                wait_seconds = min(
                    SSE_IDLE_CHECK_SECONDS,
                    max(0.1, 15 - (now - last_sent)),
                    max(0.1, config.SSE_CONNECTION_TTL_SECONDS - (now - start)),
                )
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    continue
                if event.get("event") == "job":
                    job = event.get("data")
                    if not isinstance(job, dict):
                        continue
                    last_sent = time.monotonic()
                    yield serialize_sse_event("job", job)
                    continue
                if event.get("event") != "jobs":
                    continue
                jobs = event.get("data") or []
                payload = json_payload_key(jobs)
                if payload == last_payload:
                    continue
                last_payload = payload
                last_sent = time.monotonic()
                yield serialize_sse_event("jobs", jobs)
        finally:
            subscribers.discard(queue)
            await sse_limiter.release(sse_lease)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/api/generate/jobs/history", response_model=MessageResponse)
async def clear_generate_job_history():
    deleted_count = await run_db_operation(
        clear_persisted_generate_job_history,
        metric_name="clear_generate_job_history",
    )
    return MessageResponse(
        status="success",
        message=f"Deleted {deleted_count} job history entr{'y' if deleted_count == 1 else 'ies'}",
    )


@router.get("/api/generate/{job_id}", response_model=GenerateJobStatus)
async def get_generate_job(job_id: str):
    job = await resolve_generate_job_view(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return GenerateJobStatus(**job)


@router.get("/api/generate/{job_id}/diagnostics", response_model=JobDiagnosticsResponse)
async def get_generate_job_diagnostics(job_id: str):
    job = await run_db_operation(
        get_persisted_generate_job,
        job_id,
        metric_name="get_generate_job_diagnostics",
    )
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    aggregate = await run_db_operation(
        aggregate_image_job_units,
        job_id,
        metric_name="get_generate_job_diagnostics_units",
    )
    entries = [
        _unit_diagnostics_entry(unit)
        for unit in aggregate.get("units") or []
        if unit.get("diagnostics") or unit.get("remote")
    ]
    entries.sort(key=lambda entry: entry["unit_index"])
    return JobDiagnosticsResponse(job_id=job_id, units=entries[:MAX_DIAGNOSTIC_UNITS])


async def _job_mask_coverage(job_id: str) -> float | None:
    aggregate = await run_db_operation(
        aggregate_image_job_units,
        job_id,
        metric_name="get_generate_job_mask_coverage",
    )
    for unit in aggregate.get("units", []):
        for source in unit.get("edit_sources") or []:
            if source.get("role") == "mask":
                coverage = source.get("coverage")
                return float(coverage) if coverage is not None else None
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mask_response(path: Path, coverage: float | None, *, verified: bool) -> FileResponse:
    headers = {
        "Cache-Control": (
            "private, no-store" if verified else "private, max-age=31536000, immutable"
        )
    }
    if coverage is not None:
        headers["X-Mask-Coverage"] = str(coverage)
    return FileResponse(path, media_type=MASK_CONTENT_TYPE, filename="mask.png", headers=headers)


@router.post("/api/generate/{job_id}/mask/restore")
async def restore_generate_job_mask(
    job_id: str,
    source_sha256: str | None = Form(None),
    gallery_image_id: str | None = Form(None),
):
    job = await run_db_operation(
        get_persisted_generate_job, job_id, metric_name="restore_generate_job_mask"
    )
    if not job or not job.get("mask_applied"):
        raise HTTPException(status_code=404, detail="No mask is stored for this job")
    path = safe_mask_path(f"{job_id}.png")
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="Mask file not found")
    if bool(source_sha256) == bool(gallery_image_id):
        raise HTTPException(status_code=400, detail="Provide exactly one edit source identity")
    aggregate = await run_db_operation(
        aggregate_image_job_units, job_id, metric_name="restore_generate_job_sources"
    )
    primary = next(
        (
            source
            for unit in aggregate.get("units", [])
            for source in unit.get("edit_sources") or []
            if source.get("role", "image") == "image"
        ),
        None,
    )
    expected = primary.get("raw_sha256") if primary else None
    if not isinstance(expected, str) or len(expected) != 64:
        raise HTTPException(status_code=409, detail="Original edit source identity is unavailable")
    if source_sha256 is not None:
        if (
            primary.get("gallery_image_id")
            or len(source_sha256) != 64
            or any(c not in "0123456789abcdef" for c in source_sha256)
        ):
            raise HTTPException(status_code=409, detail="Edit source does not match this job")
        actual = source_sha256
    else:
        if primary.get("gallery_image_id") != gallery_image_id:
            raise HTTPException(status_code=409, detail="Edit source does not match this job")
        entry = await run_db_operation(
            get_gallery_entry, gallery_image_id, metric_name="restore_generate_gallery_source"
        )
        gallery_path = safe_image_path(entry.filename) if entry else None
        if not gallery_path or not gallery_path.is_file():
            raise HTTPException(status_code=409, detail="Original gallery image is unavailable")
        try:
            actual = await run_image_operation(
                _sha256_file, gallery_path, metric_name="hash_restore_gallery_source"
            )
        except OSError as exc:
            raise HTTPException(status_code=409, detail="Original gallery image is unavailable") from exc
    if not hmac.compare_digest(expected, actual):
        raise HTTPException(status_code=409, detail="Edit source does not match this job")
    return _mask_response(path, await _job_mask_coverage(job_id), verified=True)


@router.get("/api/generate/{job_id}/mask")
async def get_generate_job_mask(job_id: str):
    job = await run_db_operation(
        get_persisted_generate_job,
        job_id,
        metric_name="get_generate_job_mask",
    )
    if not job or not job.get("mask_applied"):
        raise HTTPException(status_code=404, detail="No mask is stored for this job")
    path = safe_mask_path(f"{job_id}.png")
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="Mask file not found")
    # job_id is unique and the file is never rewritten after promotion, so a
    # retry re-fetching the same job's mask can cache it indefinitely.
    return _mask_response(path, await _job_mask_coverage(job_id), verified=False)


@router.get("/api/generate/{job_id}/events")
async def stream_generate_job(job_id: str, request: Request):
    job = await resolve_generate_job_view(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")

    client_ip = auth.get_client_ip(request)
    sse_lease = await sse_limiter.acquire(client_ip)
    if not sse_lease:
        raise HTTPException(status_code=429, detail="Too many SSE connections")

    async def event_stream():
        start = time.monotonic()
        last_refresh_at = start
        last_payload = None
        last_sent = 0.0
        queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
        subscribers = get_job_subscribers().setdefault(job_id, set())
        subscribers.add(queue)
        _start_generate_sse_poller()
        try:
            current = await resolve_generate_job_view(job_id)
            if not current:
                return
            last_payload = json_payload_key(current)
            last_sent = time.monotonic()
            yield serialize_sse_event("job", current)
            if current.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
                return

            # Reconnect replay: send whatever partial-image preview is still
            # cached for each unit so a client that briefly dropped doesn't
            # have to wait for the next streamed frame to see one.
            for cached_preview in get_cached_generate_job_previews(job_id):
                yield serialize_sse_event("preview", cached_preview)

            while True:
                if await request.is_disconnected():
                    break
                now = time.monotonic()
                refreshed_at = await sse_limiter.refresh_if_needed(
                    sse_lease,
                    last_refresh_at,
                )
                if refreshed_at is None:
                    break
                last_refresh_at = refreshed_at
                if now - start > config.SSE_CONNECTION_TTL_SECONDS:
                    break
                if now - last_sent >= 15:
                    last_sent = now
                    yield ": keep-alive\n\n"
                    continue
                wait_seconds = min(
                    SSE_IDLE_CHECK_SECONDS,
                    max(0.1, 15 - (now - last_sent)),
                    max(0.1, config.SSE_CONNECTION_TTL_SECONDS - (now - start)),
                )
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    continue
                if event.get("event") == "_missing":
                    break
                if event.get("event") == "preview":
                    preview_payload = event.get("data")
                    if isinstance(preview_payload, dict):
                        last_sent = time.monotonic()
                        yield serialize_sse_event("preview", preview_payload)
                    continue
                if event.get("event") != "job":
                    continue
                current = event.get("data")
                if not current:
                    break
                payload = json_payload_key(current)
                if payload == last_payload:
                    continue
                last_payload = payload
                last_sent = time.monotonic()
                yield serialize_sse_event("job", current)
                if current.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
                    break
        finally:
            subscribers.discard(queue)
            if not subscribers:
                get_job_subscribers().pop(job_id, None)
            await sse_limiter.release(sse_lease)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/api/generate/{job_id}", response_model=MessageResponse)
async def cancel_generate_job(job_id: str):
    await cancel_image_job(job_id)
    return MessageResponse(status="success", message="Generation job cancelled")
