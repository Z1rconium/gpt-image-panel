"""Generation job state, persistence, and SSE publication."""

import asyncio
import json
import logging
import time

from fastapi import HTTPException

from ..runtime.state import (
    GENERATE_JOB_PERSIST_INTERVAL_SECONDS,
    GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS,
    state,
)
from ..core import settings as config
from ..core import validators as ssrf
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.observability import metrics
from ..core.utils import utc_now
from ..repositories.image_jobs import (
    get_generate_job,
    list_generate_jobs,
    pop_generate_job_webhook,
    transition_generate_job_to_terminal,
    upsert_generate_job_guarded,
)
from . import webhook_service as webhooks
from .blocking import run_db_operation


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


def get_generate_job_preview_cache() -> dict[tuple[str, int], dict]:
    """In-memory, per-process only: one slot per (job_id, unit_index) holding
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
    key = (job_id, unit_index)
    cache[key] = payload
    while len(cache) > config.PREVIEW_CACHE_MAX_ENTRIES:
        oldest_key = next(iter(cache))
        if oldest_key == key:
            break
        cache.pop(oldest_key, None)
    metrics.increment("image_job.streaming_preview_received")

    event = {"event": "preview", "data": payload}
    for queue in list(get_job_subscribers().get(job_id, set())):
        publish_queue(queue, event)


def get_cached_generate_job_previews(job_id: str) -> list[dict]:
    cache = get_generate_job_preview_cache()
    return [payload for (cached_job_id, _unit_index), payload in cache.items() if cached_job_id == job_id]


def clear_generate_job_preview_cache(job_id: str) -> None:
    cache = get_generate_job_preview_cache()
    for key in [key for key in cache if key[0] == job_id]:
        cache.pop(key, None)


def serialize_sse_event(event: str, data: dict | list) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


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


def validate_job_webhook_url(webhook_url: str | None) -> str | None:
    normalized_url = str(webhook_url or "").strip()
    if not normalized_url:
        return None
    try:
        ssrf.validate_webhook_url(normalized_url, config.WEBHOOK_HOST_ALLOWLIST)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if len(config.WEBHOOK_SIGNING_SECRET.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=422,
            detail="WEBHOOK_SIGNING_SECRET must contain at least 32 bytes",
        )
    return normalized_url


def get_webhook_delivery_tasks() -> set[asyncio.Task]:
    tasks = getattr(state, "webhook_delivery_tasks", None)
    if not isinstance(tasks, set):
        tasks = set()
        state.webhook_delivery_tasks = tasks
    return tasks


def _track_webhook_delivery_task(task: asyncio.Task) -> asyncio.Task:
    tasks = get_webhook_delivery_tasks()
    tasks.add(task)

    def discard(completed: asyncio.Task) -> None:
        tasks.discard(completed)
        if completed.cancelled():
            return
        try:
            completed.result()
        except Exception:
            logger.warning("Webhook delivery task failed", exc_info=True)

    task.add_done_callback(discard)
    return task


def _create_webhook_delivery_task(webhook_url: str, job: dict) -> asyncio.Task:
    return _track_webhook_delivery_task(
        asyncio.create_task(webhooks.deliver_webhook(webhook_url, public_generate_job(job)))
    )


def dispatch_job_webhook(job: dict):
    webhook_url = pop_generate_job_webhook(job["job_id"])
    if not webhook_url:
        return
    _create_webhook_delivery_task(webhook_url, job)


async def dispatch_job_webhook_async(job: dict):
    webhook_url = await run_db_operation(
        pop_generate_job_webhook,
        job["job_id"],
        metric_name="pop_generate_job_webhook",
    )
    if webhook_url:
        _create_webhook_delivery_task(webhook_url, job)


def build_job_update(job_id: str, updates: dict) -> dict:
    now = utc_now()
    existing = state.generate_jobs.get(job_id) or get_generate_job(job_id) or {}
    job = {
        **existing,
        **updates,
        "job_id": job_id,
        "updated_at": now,
    }
    if "created_at" not in job:
        job["created_at"] = now
    if job.get("image_id"):
        job["id"] = job["image_id"]
    return job


def should_persist_generate_job(job_id: str, job: dict, persist: bool) -> bool:
    if persist:
        return True
    if job.get("status") != "running":
        return True

    last_persist_at = state.generate_job_last_persist_at
    now = time.monotonic()
    previous = last_persist_at.get(job_id)
    if previous is None or now - previous >= GENERATE_JOB_PERSIST_INTERVAL_SECONDS:
        last_persist_at[job_id] = now
        return True
    return False


def _publish_generate_job_row_sync(job: dict, *, dispatch_webhook: bool) -> dict:
    is_terminal = job.get("status") not in ACTIVE_GENERATE_JOB_STATUSES
    if is_terminal:
        drop_generate_job_memory(str(job["job_id"]))
    else:
        remember_generate_job_memory(str(job["job_id"]), job)
    publish_generate_job(job, list_debounce=not is_terminal, list_reconcile=False)
    if is_terminal and dispatch_webhook:
        dispatch_job_webhook(job)
    return job


async def publish_generate_job_row_async(job: dict, *, dispatch_webhook: bool) -> dict:
    """Apply an authoritative parent row to memory and publish it.

    Used after a guarded repository write so the in-memory cache always
    reflects what SQLite actually stored. The webhook is dispatched only by the
    call that actually wrote the terminal state.
    """
    is_terminal = job.get("status") not in ACTIVE_GENERATE_JOB_STATUSES
    if is_terminal:
        drop_generate_job_memory(str(job["job_id"]))
    else:
        remember_generate_job_memory(str(job["job_id"]), job)
    publish_generate_job(job, list_debounce=not is_terminal, list_reconcile=False)
    if is_terminal and dispatch_webhook:
        await dispatch_job_webhook_async(job)
    return job


def store_generate_job(job_id: str, updates: dict, *, persist: bool = True) -> dict:
    job = build_job_update(job_id, updates)
    status = job.get("status")
    if status not in ACTIVE_GENERATE_JOB_STATUSES:
        row, written = transition_generate_job_to_terminal(job_id, updates)
        if row is None:
            drop_generate_job_memory(job_id)
            publish_generate_job(job, list_debounce=False, list_reconcile=False)
            dispatch_job_webhook(job)
            return job
        return _publish_generate_job_row_sync(row, dispatch_webhook=written)

    if should_persist_generate_job(job_id, job, persist):
        _stored, wrote = upsert_generate_job_guarded(job)
        if not wrote:
            db_row = get_generate_job(job_id)
            if db_row is not None:
                return _publish_generate_job_row_sync(db_row, dispatch_webhook=False)
    remember_generate_job_memory(job_id, job)
    publish_generate_job(job, list_debounce=True, list_reconcile=False)
    return job


async def store_generate_job_async(
    job_id: str,
    updates: dict,
    *,
    persist: bool = True,
) -> dict:
    existing = state.generate_jobs.get(job_id)
    if existing is None:
        existing = await run_db_operation(
            get_generate_job,
            job_id,
            metric_name="get_generate_job_for_update",
        ) or {}
    now = utc_now()
    job = {**existing, **updates, "job_id": job_id, "updated_at": now}
    if "created_at" not in job:
        job["created_at"] = now
    if job.get("image_id"):
        job["id"] = job["image_id"]

    status = job.get("status")
    if status not in ACTIVE_GENERATE_JOB_STATUSES:
        # Terminal writes merge onto the stored row inside one transaction and
        # only apply while the job is still active, so a stale caller can never
        # revert a finished job (defect D3).
        row, written = await run_db_operation(
            transition_generate_job_to_terminal,
            job_id,
            updates,
            metric_name="transition_generate_job_terminal",
        )
        if row is None:
            drop_generate_job_memory(job_id)
            publish_generate_job(job, list_debounce=False, list_reconcile=False)
            await dispatch_job_webhook_async(job)
            return job
        return await publish_generate_job_row_async(row, dispatch_webhook=written)

    if should_persist_generate_job(job_id, job, persist):
        _stored, wrote = await run_db_operation(
            upsert_generate_job_guarded,
            job,
            metric_name="persist_generate_job",
        )
        if not wrote:
            # The stored row is already terminal; do not revive it in memory.
            db_row = await run_db_operation(
                get_generate_job,
                job_id,
                metric_name="reload_terminal_generate_job",
            )
            if db_row is not None:
                return await publish_generate_job_row_async(
                    db_row, dispatch_webhook=False
                )

    remember_generate_job_memory(job_id, job)
    publish_generate_job(job, list_debounce=True, list_reconcile=False)
    return job


async def resolve_generate_job_view(job_id: str) -> dict | None:
    """Resolve a job for decision-making reads (GET, SSE first frame, cancel).

    SQLite is authoritative. The in-memory copy is only overlaid while both
    the memory and storage copies are active and the memory copy is at least as
    fresh, so an executing worker keeps publishing high-frequency stage/message
    without a stale copy ever masking a terminal row (defect D4).
    """
    db_job = await run_db_operation(
        get_generate_job,
        job_id,
        metric_name="resolve_generate_job_view",
    )
    memory = state.generate_jobs.get(job_id)
    if db_job is None:
        return memory
    if db_job.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
        if memory is not None:
            drop_generate_job_memory(job_id)
        return db_job
    if (
        memory is not None
        and memory.get("status") in ACTIVE_GENERATE_JOB_STATUSES
        and str(memory.get("updated_at") or "") >= str(db_job.get("updated_at") or "")
    ):
        return {**db_job, **memory}
    # Only correct an existing cache entry; never create one for a storage-only
    # job, otherwise a non-executing worker would leak a copy per GET/SSE that
    # is never popped once another worker finishes the job (defect R2).
    if memory is not None:
        remember_generate_job_memory(job_id, {**memory, **db_job})
    return db_job
