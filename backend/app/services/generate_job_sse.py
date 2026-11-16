"""SSE streams and cross-worker edge feed for image generation jobs."""

from __future__ import annotations

import asyncio

from ..core import settings as config
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.errors import NotFoundError
from ..core.streaming import StreamedBody
from ..core.utils import json_dumps_canonical
from ..repositories.db.execution import run_db_operation
from ..repositories.image_jobs import (
    get_generate_job as get_persisted_generate_job,
    get_generate_sse_edges,
    list_generate_jobs as list_persisted_generate_jobs,
)
from ..runtime.state import state
from .job_events import (
    get_cached_generate_job_previews,
    reconcile_active_generate_jobs,
    serialize_sse_event,
)
from .job_persistence import resolve_generate_job_view
from .sse_feed import KeyedFeed, SseConnection

SSE_IDLE_CHECK_SECONDS = 1.0
SSE_QUEUE_MAXSIZE = 20
SSE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Accel-Buffering": "no",
}


def json_payload_key(payload: dict | list) -> str:
    return json_dumps_canonical(payload, compact=True)


async def _read_generate_edges(
    job_ids: set[str],
    include_list_edge: bool,
) -> tuple[tuple[int, str] | None, dict[str, str]]:
    return await run_db_operation(
        get_generate_sse_edges,
        list_statuses=ACTIVE_GENERATE_JOB_STATUSES,
        job_ids=job_ids or None,
        include_list_edge=include_list_edge,
        metric_name="poll_generate_sse_edges",
    )


async def _read_generate_list_event() -> dict:
    jobs = await run_db_operation(
        list_persisted_generate_jobs,
        statuses=ACTIVE_GENERATE_JOB_STATUSES,
        metric_name="poll_generate_jobs",
    )
    return {"event": "jobs", "data": reconcile_active_generate_jobs(jobs)}


async def _read_generate_job_event(job_id: str) -> dict:
    job = await run_db_operation(
        get_persisted_generate_job,
        job_id,
        metric_name="poll_generate_job",
    )
    return {"event": "job", "data": job} if job else {"event": "_missing", "data": None}


generate_feed = KeyedFeed(
    name="Generate",
    get_keyed_subscribers=lambda: state.generate_job_subscribers,
    get_list_subscribers=lambda: state.generate_jobs_subscribers,
    get_poller_task=lambda: state.generate_jobs_sse_poller_task,
    set_poller_task=lambda task: setattr(state, "generate_jobs_sse_poller_task", task),
    read_edges=_read_generate_edges,
    read_event=_read_generate_job_event,
    read_list_event=_read_generate_list_event,
    poll_metric_name="sse.poll_queries.generate",
    base_interval=lambda: config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS,
    max_backoff=lambda: config.SSE_IDLE_BACKOFF_MAX_SECONDS,
    default_queue_maxsize=SSE_QUEUE_MAXSIZE,
)


async def stream_generate_jobs(*, client_ip: str, is_disconnected) -> StreamedBody:
    conn = await SseConnection.open(client_ip, is_disconnected=is_disconnected)

    async def event_stream():
        last_payload = None
        try:
            async with generate_feed.subscribe(None, maxsize=SSE_QUEUE_MAXSIZE) as queue:
                jobs = await run_db_operation(
                    list_persisted_generate_jobs,
                    statuses=ACTIVE_GENERATE_JOB_STATUSES,
                    metric_name="stream_generate_jobs_initial",
                )
                jobs = reconcile_active_generate_jobs(jobs)
                last_payload = json_payload_key(jobs)
                conn.mark_sent()
                yield serialize_sse_event("jobs", jobs)

                while True:
                    action = await conn.heartbeat()
                    if action == "stop":
                        break
                    if action == "keepalive":
                        yield SseConnection.KEEPALIVE_FRAME
                        continue
                    wait_seconds = conn.wait_timeout(SSE_IDLE_CHECK_SECONDS)
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                    except asyncio.TimeoutError:
                        continue
                    if event.get("event") == "job":
                        job = event.get("data")
                        if not isinstance(job, dict):
                            continue
                        conn.mark_sent()
                        yield serialize_sse_event("job", job)
                        continue
                    if event.get("event") != "jobs":
                        continue
                    jobs = event.get("data") or []
                    payload = json_payload_key(jobs)
                    if payload == last_payload:
                        continue
                    last_payload = payload
                    conn.mark_sent()
                    yield serialize_sse_event("jobs", jobs)
        finally:
            await conn.close()

    return StreamedBody(
        chunks=event_stream(),
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )


async def stream_generate_job(*, job_id: str, client_ip: str, is_disconnected) -> StreamedBody:
    job = await resolve_generate_job_view(job_id)
    if not job:
        raise NotFoundError("Generation job not found")

    conn = await SseConnection.open(client_ip, is_disconnected=is_disconnected)

    async def event_stream():
        last_payload = None
        try:
            async with generate_feed.subscribe(job_id, maxsize=SSE_QUEUE_MAXSIZE) as queue:
                current = await resolve_generate_job_view(job_id)
                if not current:
                    return
                last_payload = json_payload_key(current)
                conn.mark_sent()
                yield serialize_sse_event("job", current)
                if current.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
                    return

                # Reconnect replay: send whatever partial-image preview is still
                # cached for each unit so a client that briefly dropped doesn't
                # have to wait for the next streamed frame to see one.
                for cached_preview in get_cached_generate_job_previews(job_id):
                    yield serialize_sse_event("preview", cached_preview)

                while True:
                    action = await conn.heartbeat()
                    if action == "stop":
                        break
                    if action == "keepalive":
                        yield SseConnection.KEEPALIVE_FRAME
                        continue
                    wait_seconds = conn.wait_timeout(SSE_IDLE_CHECK_SECONDS)
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                    except asyncio.TimeoutError:
                        continue
                    if event.get("event") == "_missing":
                        break
                    if event.get("event") == "preview":
                        preview_payload = event.get("data")
                        if isinstance(preview_payload, dict):
                            conn.mark_sent()
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
                    conn.mark_sent()
                    yield serialize_sse_event("job", current)
                    if current.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
                        break
        finally:
            await conn.close()

    return StreamedBody(
        chunks=event_stream(),
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )
