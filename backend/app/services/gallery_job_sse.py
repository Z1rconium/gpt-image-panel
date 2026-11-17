"""Per-process SSE fan-out for gallery jobs.

Subscribers are per worker process; updates written by another worker are
picked up by polling each job's updated_at edge (see job_events)."""

import asyncio
from collections.abc import Collection

from ..core import settings as config
from ..core.errors import NotFoundError
from ..core.streaming import StreamedBody
from ..repositories.coordination import (
    get_gallery_job,
    get_gallery_jobs_updated_at_edges,
)
from ..repositories.db.execution import run_db_operation
from ..runtime.state import state
from .gallery_common import (
    GALLERY_JOB_DISPATCH_INTERVAL_SECONDS,
    GALLERY_JOB_SSE_IDLE_CHECK_SECONDS,
    GALLERY_JOB_SSE_QUEUE_MAXSIZE,
    PRIVATE_GALLERY_CACHE_CONTROL,
)
from .gallery_job_kinds import (
    gallery_job_event_name,
    gallery_job_payload,
    get_gallery_job_kind,
)
from .job_events import serialize_sse_event
from .sse_feed import KeyedFeed, SseConnection

_GALLERY_FEEDS: dict[str, KeyedFeed] = {}


def _get_gallery_job_subscribers(kind: str) -> dict[str, set[asyncio.Queue]]:
    return state.gallery_job_subscribers.setdefault(kind, {})


def gallery_feed(kind: str) -> KeyedFeed:
    """Return the shared keyed SSE feed for gallery jobs of ``kind``."""
    feed = _GALLERY_FEEDS.get(kind)
    if feed is not None:
        return feed

    async def read_edges(job_ids: set[str], _include_list_edge: bool) -> tuple[None, dict[str, str]]:
        edges = await run_db_operation(
            get_gallery_jobs_updated_at_edges,
            kind,
            job_ids,
        )
        return None, edges

    async def read_event(job_id: str) -> dict:
        job = await run_db_operation(get_gallery_job, kind, job_id)
        return (
            {
                "event": gallery_job_event_name(kind),
                "data": gallery_job_payload(kind, job),
            }
            if job
            else {"event": "_missing", "data": None}
        )

    feed = KeyedFeed(
        name=f"Gallery {kind}",
        get_keyed_subscribers=lambda: state.gallery_job_subscribers.setdefault(kind, {}),
        get_poller_task=lambda: state.gallery_job_sse_poller_tasks.get(kind),
        set_poller_task=lambda task: (
            state.gallery_job_sse_poller_tasks.__setitem__(kind, task)
            if task is not None
            else state.gallery_job_sse_poller_tasks.pop(kind, None)
        ),
        read_edges=read_edges,
        read_event=read_event,
        poll_metric_name=f"sse.poll_queries.gallery_{kind}",
        base_interval=GALLERY_JOB_DISPATCH_INTERVAL_SECONDS,
        max_backoff=lambda: config.SSE_IDLE_BACKOFF_MAX_SECONDS,
        default_queue_maxsize=GALLERY_JOB_SSE_QUEUE_MAXSIZE,
    )
    _GALLERY_FEEDS[kind] = feed
    return feed


def _publish_gallery_job_sse(job: dict) -> None:
    kind = str(job.get("kind") or "")
    job_id = str(job.get("job_id") or "")
    if not kind or not job_id:
        return
    gallery_feed(kind).publish(
        job_id,
        {
            "event": gallery_job_event_name(kind),
            "data": gallery_job_payload(kind, job),
        },
    )


async def stream_gallery_job(
    *,
    kind: str,
    job_id: str,
    client_ip: str,
    is_disconnected,
    event_name: str | None = None,
    terminal_statuses: Collection[str] | None = None,
    payload_builder=None,
    not_found_detail: str | None = None,
) -> StreamedBody:
    descriptor = get_gallery_job_kind(kind)
    resolved_event_name = event_name if event_name is not None else descriptor.event_name
    resolved_terminal_statuses = (
        terminal_statuses if terminal_statuses is not None else descriptor.terminal_statuses
    )
    resolved_payload_builder = payload_builder if payload_builder is not None else descriptor.payload
    resolved_not_found_detail = (
        not_found_detail if not_found_detail is not None else descriptor.not_found_detail
    )

    job = await run_db_operation(get_gallery_job, kind, job_id)
    if not job:
        raise NotFoundError(resolved_not_found_detail)

    conn = await SseConnection.open(client_ip, is_disconnected=is_disconnected)
    feed = gallery_feed(kind)

    async def event_stream():
        last_updated_at: str | None = None
        try:
            async with feed.subscribe(job_id, maxsize=GALLERY_JOB_SSE_QUEUE_MAXSIZE) as queue:
                current_job = await run_db_operation(get_gallery_job, kind, job_id)
                if not current_job:
                    return
                payload = resolved_payload_builder(current_job)
                last_updated_at = str(payload.get("updated_at") or "")
                conn.mark_sent()
                yield serialize_sse_event(resolved_event_name, payload)
                if payload.get("status") in resolved_terminal_statuses:
                    return

                while True:
                    action = await conn.heartbeat()
                    if action == "stop":
                        break
                    if action == "keepalive":
                        yield SseConnection.KEEPALIVE_FRAME
                        continue
                    wait_seconds = conn.wait_timeout(GALLERY_JOB_SSE_IDLE_CHECK_SECONDS)
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                    except asyncio.TimeoutError:
                        continue
                    if event.get("event") == "_missing":
                        break
                    if event.get("event") != resolved_event_name:
                        continue
                    payload = event.get("data")
                    if not isinstance(payload, dict):
                        break
                    updated_at = str(payload.get("updated_at") or "")
                    if updated_at == last_updated_at:
                        continue
                    last_updated_at = updated_at
                    conn.mark_sent()
                    yield serialize_sse_event(resolved_event_name, payload)
                    if payload.get("status") in resolved_terminal_statuses:
                        break
        finally:
            await conn.close()

    return StreamedBody(
        chunks=event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": PRIVATE_GALLERY_CACHE_CONTROL,
            "X-Accel-Buffering": "no",
        },
    )
