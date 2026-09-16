"""Lease, background-lease, and progress-publishing helpers shared by the
per-kind gallery job runners."""

import asyncio
import logging
import time

from .gallery_job_sse import (
    _publish_gallery_job_sse,
)


from ..runtime.blocking import run_db_operation, run_db_operation_in_current_thread
from ..runtime.state import utc_lease_expires_at
from ..repositories.coordination import (
    acquire_background_lease,
    update_gallery_job,
    update_gallery_job_progress,
)
from .gallery_common import (
    BACKGROUND_TASK_ERROR_BACKOFF_INITIAL_SECONDS,
    BACKGROUND_TASK_ERROR_BACKOFF_MAX_SECONDS,
    BACKGROUND_TASK_LEASE_SECONDS,
    DIRECT_EXPORT_SLOT_LEASE_SECONDS,
    GALLERY_JOB_LEASE_SECONDS,
)
logger = logging.getLogger(__name__)


def _gallery_job_lease_expires_at() -> str:
    return utc_lease_expires_at(GALLERY_JOB_LEASE_SECONDS)


def _direct_export_slot_expires_at() -> str:
    return utc_lease_expires_at(DIRECT_EXPORT_SLOT_LEASE_SECONDS)


def _background_task_lease_expires_at() -> str:
    return utc_lease_expires_at(BACKGROUND_TASK_LEASE_SECONDS)


def _next_background_task_error_backoff(current: float) -> float:
    if current <= 0:
        return BACKGROUND_TASK_ERROR_BACKOFF_INITIAL_SECONDS
    return min(current * 2, BACKGROUND_TASK_ERROR_BACKOFF_MAX_SECONDS)


async def _sleep_while_renewing_background_lease(
    *,
    name: str,
    owner: str,
    delay_seconds: float,
) -> bool:
    deadline = time.monotonic() + max(0.0, float(delay_seconds or 0))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(remaining, max(1.0, BACKGROUND_TASK_LEASE_SECONDS / 2)))
        renewed = await asyncio.to_thread(
            acquire_background_lease,
            name=name,
            owner=owner,
            lease_expires_at=_background_task_lease_expires_at(),
        )
        if not renewed:
            return False


def _claim_counted_gallery_kinds(kind: str) -> tuple[str, ...]:
    if kind == "export":
        return ("export", "export_direct")
    return (kind,)


async def _publish_gallery_job(job_id: str, updates: dict) -> dict | None:
    job = await run_db_operation(
        update_gallery_job,
        job_id,
        updates,
        metric_name="update_gallery_job",
    )
    if job:
        _publish_gallery_job_sse(job)
    return job


async def _publish_gallery_job_progress(job_id: str, updates: dict) -> bool:
    return await run_db_operation(
        update_gallery_job_progress,
        job_id,
        updates,
        metric_name="update_gallery_job_progress",
    )


def _publish_gallery_job_progress_from_worker(job_id: str, updates: dict) -> bool:
    try:
        return run_db_operation_in_current_thread(
            update_gallery_job_progress,
            job_id,
            updates,
            metric_name="update_gallery_job_progress",
        )
    except Exception:
        # Progress persistence is best effort; a transient DB failure must not
        # abort the export, sync, import, or direct ZIP stream itself.
        logger.warning(
            "Failed to persist gallery job progress for %s",
            job_id,
            exc_info=True,
        )
        return False


def _publish_gallery_job_from_worker(job_id: str, updates: dict) -> dict | None:
    return run_db_operation_in_current_thread(
        update_gallery_job,
        job_id,
        updates,
        metric_name="update_gallery_job",
    )
