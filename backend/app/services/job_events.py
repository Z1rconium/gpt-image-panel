"""Generation job state and SSE publication.

Job persistence and view resolution live in
:mod:`app.services.job_persistence`; webhook dispatch lives in
:mod:`app.services.webhook_service` (F-G5-2). This module owns only the
per-process memory cache, subscribers, and SSE fan-out.
"""

import asyncio
import json
import logging
import time


from ..runtime.state import (
    GENERATE_JOB_PERSIST_INTERVAL_SECONDS,
    GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS,
    state,
)
from ..core import settings as config
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.observability import metrics
from ..repositories.image_jobs import list_generate_jobs
from ..repositories.db.execution import run_db_operation


logger = logging.getLogger(__name__)

def get_job_subscribers() -> dict[str, set[asyncio.Queue]]:
    subscribers = getattr(state, "generate_job_subscribers", None)
    if not isinstance(subscribers, dict):
        subscribers = {}
        state.generate_job_subscribers = subscribers
    return subscribers


def get_jobs_subscribers() -> set[asyncio.Queue]:
    subscribers = getattr(state, "generate_jobs_subscribers", None)
    if not isinstance(subscribers, set):
        subscribers = set()
        state.generate_jobs_subscribers = subscribers
    return subscribers


def get_generate_job_preview_cache() -> dict[tuple[str, int, int], dict]:
    """In-memory, per-process only: one slot per (job_id, unit_index, call_index) holding
    the most recent partial-image preview. Never persisted to SQLite - a
    reconnecting client either gets this cached frame or waits for the next
    one; a restarted/other worker process has nothing to replay, which is an
    accepted tradeoff for keeping base64 image data out of shared storage."""
    cache = getattr(state, "generate_job_preview_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        state.generate_job_preview_cache = cache
    return cache


def publish_generate_job_preview(job_id: str, payload: dict) -> None:
    """Publish a streamed partial-image preview to subscribers of this one
    job's SSE stream only - never to the jobs-list feed."""
    try:
        unit_index = int(payload.get("unit_index") or 0)
    except (TypeError, ValueError):
        unit_index = 0

    data_url = str(payload.get("data_url") or "")
    if len(data_url.encode("utf-8")) > config.PREVIEW_CACHE_MAX_ENTRY_MB * 1024 * 1024:
        metrics.increment("image_job.streaming_preview_dropped")
        return

    cache = get_generate_job_preview_cache()
    call_index = max(0, min(9, int(payload.get("call_index") or 0)))
    key = (job_id, unit_index, call_index)
    cache[key] = payload
    while len(cache) > config.PREVIEW_CACHE_MAX_ENTRIES:
        oldest_key = next(iter(cache))
        if oldest_key == key:
            break
        cache.pop(oldest_key, None)
    metrics.increment("image_job.streaming_preview_received")

    event = {"event": "preview", "data": payload}
    for queue in list(get_job_subscribers().get(job_id, set())):
        # Replace queued frames for this slot so slow clients retain one latest
        # frame rather than every base64 payload; preserve job/status events.
        pending = []
        while True:
            try:
                queued = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            data = queued.get("data") or {}
            if queued.get("event") != "preview" or (data.get("unit_index", 0), data.get("call_index", 0)) != (unit_index, call_index):
                pending.append(queued)
        for queued in pending:
            publish_queue(queue, queued)
        publish_queue(queue, event)


def get_cached_generate_job_previews(job_id: str) -> list[dict]:
    cache = get_generate_job_preview_cache()
    return [payload for key, payload in cache.items() if key[0] == job_id]


def clear_generate_job_preview_cache(job_id: str) -> None:
    cache = get_generate_job_preview_cache()
    for key in [key for key in cache if key[0] == job_id]:
        cache.pop(key, None)


def clear_generate_job_unit_previews(job_id: str, unit_index: int) -> None:
    cache = get_generate_job_preview_cache()
    for key in [key for key in cache if key[0] == job_id and key[1] == unit_index]:
        cache.pop(key, None)


def serialize_sse_event(event: str, data: dict | list, *, event_id: int | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{id_line}event: {event}\ndata: {payload}\n\n"


def publish_queue(queue: asyncio.Queue, event: dict):
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def publish_job_edges(
    *,
    subscribers_by_job: dict[str, list[asyncio.Queue]],
    edges: dict[str, str],
    last_edges: dict[str, str],
    read_event,
) -> bool:
    """Fan out one poll cycle of per-job edge changes.

    A job with subscribers but no edge row is gone: its subscribers get a
    ``_missing`` event so their streams can close. Changed rows ask
    ``read_event`` for the payload to publish. Returns whether anything was
    sent, which the callers use to reset their idle backoff.
    """
    changed = False
    for job_id in set(subscribers_by_job) - set(edges):
        for queue in subscribers_by_job[job_id]:
            publish_queue(queue, {"event": "_missing", "data": None})
        last_edges.pop(job_id, None)
        changed = True

    for job_id in (
        job_id
        for job_id, updated_at in edges.items()
        if last_edges.get(job_id) != updated_at
    ):
        event = await read_event(job_id)
        for queue in subscribers_by_job.get(job_id, []):
            publish_queue(queue, event)
        changed = True

    last_edges.clear()
    last_edges.update(edges)
    return changed


def publish_generate_job(
    job: dict,
    *,
    list_debounce: bool = True,
    list_reconcile: bool = False,
):
    event = {"event": "job", "data": public_generate_job(job)}
    for queue in list(get_job_subscribers().get(job["job_id"], set())):
        publish_queue(queue, event)
    if job.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
        for queue in list(get_jobs_subscribers()):
            publish_queue(queue, event)
    publish_generate_jobs(debounce=list_debounce, reconcile=list_reconcile)


def sort_generate_jobs(jobs: list[dict]) -> list[dict]:
    jobs.sort(
        key=lambda job: job.get("updated_at") or job.get("created_at", ""),
        reverse=True,
    )
    return jobs


def get_generate_job_seen_at() -> dict[str, float]:
    """Per-process monotonic timestamps of the last local memory write.

    Tracked in a side table rather than inside the job dict so it never leaks
    into API responses or SSE payloads.
    """
    seen = getattr(state, "generate_job_seen_at", None)
    if not isinstance(seen, dict):
        seen = {}
        state.generate_job_seen_at = seen
    return seen


def remember_generate_job_memory(job_id: str, job: dict) -> None:
    state.generate_jobs[job_id] = job
    get_generate_job_seen_at()[job_id] = time.monotonic()


def drop_generate_job_memory(job_id: str) -> None:
    state.generate_jobs.pop(job_id, None)
    state.generate_job_last_persist_at.pop(job_id, None)
    get_generate_job_seen_at().pop(job_id, None)
    clear_generate_job_preview_cache(job_id)


def has_stale_generate_job_memory() -> bool:
    """True when this process holds an active memory copy it has not touched
    for two persist windows, so another worker (or a finished job) likely owns
    the real state."""
    now = time.monotonic()
    jobs = getattr(state, "generate_jobs", {}) or {}
    seen = get_generate_job_seen_at()
    return any(
        now - seen.get(job_id, 0.0) > 2 * GENERATE_JOB_PERSIST_INTERVAL_SECONDS
        for job_id, job in jobs.items()
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES
    )


async def reconcile_stale_generate_job_memory() -> bool:
    """Refresh the memory cache from storage on a periodic async hook.

    Runs on every worker and does not depend on a list SSE subscriber, which is
    what the old snapshot-time reconcile could never guarantee (defect R3).
    """
    if not has_stale_generate_job_memory():
        return False
    storage_jobs = await run_db_operation(
        list_generate_jobs,
        statuses=ACTIVE_GENERATE_JOB_STATUSES,
        metric_name="reconcile_stale_generate_jobs",
    )
    reconcile_active_generate_jobs(storage_jobs)
    metrics.increment("image_jobs.stale_memory_reconciled")
    return True


def snapshot_active_generate_jobs_from_memory() -> list[dict]:
    generate_jobs = getattr(state, "generate_jobs", {})
    jobs = [
        public_generate_job(job)
        for job in generate_jobs.values()
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES
    ]
    return sort_generate_jobs(jobs)


def reconcile_active_generate_jobs(storage_jobs: list[dict]) -> list[dict]:
    jobs_by_id = {job["job_id"]: job for job in storage_jobs}
    local_jobs = getattr(state, "generate_jobs", {})
    for job_id, job in local_jobs.items():
        if job.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
            continue
        if job_id not in jobs_by_id:
            continue
        storage_job = jobs_by_id[job_id]
        if str(job.get("updated_at") or "") >= str(storage_job.get("updated_at") or ""):
            jobs_by_id[job_id] = job
    state.generate_jobs = jobs_by_id
    seen = get_generate_job_seen_at()
    now = time.monotonic()
    for job_id in jobs_by_id:
        seen[job_id] = now
    return sort_generate_jobs(
        [
            public_generate_job(job)
            for job in jobs_by_id.values()
            if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES
        ]
    )


def reconcile_active_generate_jobs_from_storage() -> list[dict]:
    return reconcile_active_generate_jobs(
        list_generate_jobs(statuses=ACTIVE_GENERATE_JOB_STATUSES)
    )


def list_active_generate_jobs(*, reconcile: bool = False) -> list[dict]:
    if reconcile:
        return reconcile_active_generate_jobs_from_storage()
    return snapshot_active_generate_jobs_from_memory()


def publish_generate_jobs_now(*, reconcile: bool = False):
    jobs = list_active_generate_jobs(reconcile=reconcile)
    event = {"event": "jobs", "data": jobs}
    for queue in list(get_jobs_subscribers()):
        publish_queue(queue, event)


async def publish_generate_jobs_debounced():
    try:
        await asyncio.sleep(GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS)
        reconcile = bool(state.generate_jobs_broadcast_reconcile)
        state.generate_jobs_broadcast_reconcile = False
        publish_generate_jobs_now(reconcile=reconcile)
    finally:
        if state.generate_jobs_broadcast_task is asyncio.current_task():
            state.generate_jobs_broadcast_task = None


def cancel_pending_generate_jobs_broadcast():
    task = state.generate_jobs_broadcast_task
    if task and not task.done():
        task.cancel()
    state.generate_jobs_broadcast_task = None
    state.generate_jobs_broadcast_reconcile = False


def publish_generate_jobs(*, debounce: bool = True, reconcile: bool = False):
    if not get_jobs_subscribers():
        return

    if not debounce:
        cancel_pending_generate_jobs_broadcast()
        publish_generate_jobs_now(reconcile=reconcile)
        return

    if reconcile:
        state.generate_jobs_broadcast_reconcile = True

    task = state.generate_jobs_broadcast_task
    if task and not task.done():
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        publish_generate_jobs_now(reconcile=reconcile)
        return

    state.generate_jobs_broadcast_task = loop.create_task(
        publish_generate_jobs_debounced()
    )


def public_generate_job(job: dict) -> dict:
    result = job.copy()
    result.pop("webhook_url", None)
    return result
