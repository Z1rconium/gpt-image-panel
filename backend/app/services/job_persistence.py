"""Authoritative generation-job persistence and view resolution.

Split out of :mod:`app.services.job_events`, which now owns only the in-process
state and SSE publication (F-G5-2). Here a job update is merged onto its stored
row, written through the guarded repository calls, and resolved for
decision-making reads (GET, SSE first frame, cancel). Webhook dispatch lives in
:mod:`app.services.webhook_service`.
"""

import time
from collections.abc import Iterable

from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.utils import utc_now
from ..repositories.db.execution import run_db_operation
from ..repositories.image_jobs import (
    get_generate_job,
    get_generate_jobs_by_ids,
    transition_generate_job_to_terminal,
    upsert_generate_job_guarded,
)
from ..runtime.state import GENERATE_JOB_PERSIST_INTERVAL_SECONDS, state
from .job_events import (
    drop_generate_job_memory,
    publish_generate_job,
    remember_generate_job_memory,
)
from .webhook_service import dispatch_job_webhook, dispatch_job_webhook_async


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


def _overlay_generate_job_view(job_id: str, db_job: dict | None) -> dict | None:
    """Merge the in-memory copy over a storage row without masking terminal state.

    SQLite stays authoritative; the memory copy is only overlaid while both
    copies are active and memory is at least as fresh (defect D4). A storage-only
    job never seeds the cache, otherwise a non-executing worker would leak a copy
    per read (defect R2).
    """
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
    if memory is not None:
        remember_generate_job_memory(job_id, {**memory, **db_job})
    return db_job


async def resolve_generate_job_view(job_id: str) -> dict | None:
    """Resolve a job for decision-making reads (GET, SSE first frame, cancel)."""
    db_job = await run_db_operation(
        get_generate_job,
        job_id,
        metric_name="resolve_generate_job_view",
    )
    return _overlay_generate_job_view(job_id, db_job)


async def resolve_generate_job_views(job_ids: Iterable[str]) -> dict[str, dict | None]:
    """Resolve many jobs with a single chunked read instead of one query per job."""
    unique_ids = {str(job_id) for job_id in job_ids if job_id}
    if not unique_ids:
        return {}
    db_jobs = await run_db_operation(
        get_generate_jobs_by_ids,
        unique_ids,
        metric_name="resolve_generate_job_views",
    )
    return {
        job_id: _overlay_generate_job_view(job_id, db_jobs.get(job_id))
        for job_id in unique_ids
    }
