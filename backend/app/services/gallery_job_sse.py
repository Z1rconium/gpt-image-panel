"""Per-process SSE fan-out for gallery jobs.

from .gallery_job_payloads import (
    _gallery_job_event_name,
    _gallery_job_payload,
)

Subscribers are per worker process; updates written by another worker are
picked up by polling each job's updated_at edge (see job_events)."""

import asyncio
import time

from fastapi import (
    HTTPException,
    Request,
)
from fastapi.responses import StreamingResponse

from ..runtime.state import state
from .job_events import publish_job_edges, publish_queue, serialize_sse_event
from .poll_backoff import next_poll_delay
from ..repositories.sse_limiter import sse_limiter
from ..core import security as auth
from ..core import settings as config
from ..core.observability import metrics
from ..repositories.coordination import (
    get_gallery_job,
    get_gallery_jobs_updated_at_edges,
)
from .gallery_common import (
    GALLERY_JOB_DISPATCH_INTERVAL_SECONDS,
    GALLERY_JOB_SSE_IDLE_CHECK_SECONDS,
    GALLERY_JOB_SSE_QUEUE_MAXSIZE,
    PRIVATE_GALLERY_CACHE_CONTROL,
)


def _get_gallery_job_subscribers(kind: str) -> dict[str, set[asyncio.Queue]]:
    all_subscribers = getattr(state, "gallery_job_subscribers", None)
    if not isinstance(all_subscribers, dict):
        all_subscribers = {}
        state.gallery_job_subscribers = all_subscribers
    subscribers = all_subscribers.get(kind)
    if not isinstance(subscribers, dict):
        subscribers = {}
        all_subscribers[kind] = subscribers
    return subscribers


def _get_gallery_job_sse_poller_tasks() -> dict[str, asyncio.Task]:
    tasks = getattr(state, "gallery_job_sse_poller_tasks", None)
    if not isinstance(tasks, dict):
        tasks = {}
        state.gallery_job_sse_poller_tasks = tasks
    return tasks


def _publish_gallery_job_sse(job: dict) -> None:
    kind = str(job.get("kind") or "")
    job_id = str(job.get("job_id") or "")
    if not kind or not job_id:
        return
    subscribers = _get_gallery_job_subscribers(kind).get(job_id, set())
    if not subscribers:
        return
    event = {
        "event": _gallery_job_event_name(kind),
        "data": _gallery_job_payload(kind, job),
    }
    for queue in list(subscribers):
        publish_queue(queue, event)


def _start_gallery_job_sse_poller(kind: str) -> None:
    tasks = _get_gallery_job_sse_poller_tasks()
    task = tasks.get(kind)
    if task and not task.done():
        return
    tasks[kind] = asyncio.create_task(_poll_gallery_job_sse(kind))


async def _poll_gallery_job_sse(kind: str) -> None:
    last_edges: dict[str, str] = {}
    delay = GALLERY_JOB_DISPATCH_INTERVAL_SECONDS
    try:
        while True:
            subscribers_by_job = {
                job_id: list(subscribers)
                for job_id, subscribers in _get_gallery_job_subscribers(kind).items()
                if subscribers
            }
            if not subscribers_by_job:
                break

            current_edges = await asyncio.to_thread(
                get_gallery_jobs_updated_at_edges,
                kind,
                set(subscribers_by_job),
            )
            metrics.increment(f"sse.poll_queries.gallery_{kind}")

            async def read_event(job_id: str) -> dict:
                job = await asyncio.to_thread(get_gallery_job, kind, job_id)
                return (
                    {
                        "event": _gallery_job_event_name(kind),
                        "data": _gallery_job_payload(kind, job),
                    }
                    if job
                    else {"event": "_missing", "data": None}
                )

            changed = await publish_job_edges(
                subscribers_by_job=subscribers_by_job,
                edges=current_edges,
                last_edges=last_edges,
                read_event=read_event,
            )

            delay = next_poll_delay(
                base_interval=GALLERY_JOB_DISPATCH_INTERVAL_SECONDS,
                current_delay=delay,
                changed=changed,
                max_backoff_seconds=config.SSE_IDLE_BACKOFF_MAX_SECONDS,
            )
            if delay > GALLERY_JOB_DISPATCH_INTERVAL_SECONDS:
                metrics.increment("sse.poll_idle_backoff")
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Gallery %s SSE poller stopped after error", kind, exc_info=True)
    finally:
        tasks = _get_gallery_job_sse_poller_tasks()
        if tasks.get(kind) is asyncio.current_task():
            tasks.pop(kind, None)


async def stream_gallery_job(
    *,
    kind: str,
    job_id: str,
    request: Request,
    event_name: str,
    terminal_statuses: set[str],
    payload_builder,
    not_found_detail: str,
):
    job = await asyncio.to_thread(get_gallery_job, kind, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=not_found_detail)

    client_ip = auth.get_client_ip(request)
    sse_lease = await sse_limiter.acquire(client_ip)
    if not sse_lease:
        raise HTTPException(status_code=429, detail="Too many SSE connections")

    async def event_stream():
        start = time.monotonic()
        last_refresh_at = start
        last_updated_at: str | None = None
        last_sent = 0.0
        queue: asyncio.Queue = asyncio.Queue(maxsize=GALLERY_JOB_SSE_QUEUE_MAXSIZE)
        subscribers = _get_gallery_job_subscribers(kind).setdefault(job_id, set())
        subscribers.add(queue)
        _start_gallery_job_sse_poller(kind)
        try:
            current_job = await asyncio.to_thread(get_gallery_job, kind, job_id)
            if not current_job:
                return
            payload = payload_builder(current_job)
            last_updated_at = str(payload.get("updated_at") or "")
            last_sent = time.monotonic()
            yield serialize_sse_event(event_name, payload)
            if payload.get("status") in terminal_statuses:
                return

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
                    GALLERY_JOB_SSE_IDLE_CHECK_SECONDS,
                    max(0.1, 15 - (now - last_sent)),
                    max(0.1, config.SSE_CONNECTION_TTL_SECONDS - (now - start)),
                )
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    continue
                if event.get("event") == "_missing":
                    break
                if event.get("event") != event_name:
                    continue
                payload = event.get("data")
                if not isinstance(payload, dict):
                    break
                updated_at = str(payload.get("updated_at") or "")
                if updated_at == last_updated_at:
                    continue
                last_updated_at = updated_at
                last_sent = time.monotonic()
                yield serialize_sse_event(event_name, payload)
                if payload.get("status") in terminal_statuses:
                    break
        finally:
            subscribers.discard(queue)
            if not subscribers:
                _get_gallery_job_subscribers(kind).pop(job_id, None)
            await sse_limiter.release(sse_lease)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": PRIVATE_GALLERY_CACHE_CONTROL,
            "X-Accel-Buffering": "no",
        },
    )
