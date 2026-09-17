"""Gallery sync jobs: reconciling SQLite rows with Cloudflare R2."""

from .gallery_job_shared import (
    _gallery_job_lease_expires_at,
    _gallery_job_progress_throttler,
    _publish_gallery_job,
)

import asyncio
import inspect
import logging
import os



from ..core.errors import RateLimitedError
from ..core import settings as config
from ..core.utils import utc_now
from ..integrations.r2 import config as r2_config
from ..integrations.r2 import sync as r2_algorithm
from ..repositories.coordination import (
    reserve_gallery_job_capacity,
)
from ..repositories.gallery.sync_state import (
    count_gallery_r2_sync_rows,
    iter_gallery_r2_sync_rows,
    mark_gallery_r2_sync_state,
)
from ..repositories.settings import load_r2_backup_settings
from .gallery_common import (
    MAX_ACTIVE_SYNC_JOBS,
)
logger = logging.getLogger(__name__)


def _call_gallery_r2_sync(
    r2_settings: dict,
    entries,
    *,
    total_count: int,
    progress_cb,
    state_recorder,
    full_reconcile: bool,
    dry_run: bool,
    concurrency: int,
):
    kwargs = {
        "total_count": total_count,
        "progress_cb": progress_cb,
        "state_recorder": state_recorder,
        "full_reconcile": full_reconcile,
        "dry_run": dry_run,
        "concurrency": concurrency,
    }
    try:
        signature = inspect.signature(r2_algorithm.sync_gallery_to_r2)
    except (TypeError, ValueError):
        supported_kwargs = kwargs
    else:
        accepts_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        supported_kwargs = (
            kwargs
            if accepts_kwargs
            else {key: value for key, value in kwargs.items() if key in signature.parameters}
        )
    return r2_algorithm.sync_gallery_to_r2(r2_settings, entries, **supported_kwargs)


def _build_gallery_sync_job(total_count: int, payload: dict | None = None) -> dict:
    job_id = os.urandom(16).hex()
    now = utc_now()
    return {
        "job_id": job_id,
        "kind": "sync",
        "status": "queued",
        "stage": "queued",
        "message": "Queued R2 gallery sync",
        "progress": 0,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "total_count": total_count,
        "compared_count": 0,
        "uploaded_count": 0,
        "pending_upload_count": 0,
        "skipped_existing_count": 0,
        "missing_local_count": 0,
        "failed_count": 0,
        "bytes_total": 0,
        "bytes_uploaded": 0,
        "payload": payload or {},
    }


def _reserve_gallery_sync_job(total_count: int, payload: dict | None = None) -> dict | None:
    return reserve_gallery_job_capacity(
        job=_build_gallery_sync_job(total_count, payload),
        counted_kinds=("sync",),
        max_active=MAX_ACTIVE_SYNC_JOBS,
    )


async def _create_reserved_gallery_sync_job(
    total_count: int,
    payload: dict | None = None,
) -> dict:
    job = await asyncio.to_thread(_reserve_gallery_sync_job, total_count, payload)
    if not job:
        raise RateLimitedError("A gallery R2 sync job is already queued or running.")
    return job


async def _run_gallery_sync_job(job: dict) -> None:
    job_id = job["job_id"]
    payload = job.get("payload") or {}
    full_reconcile = bool(payload.get("full_reconcile"))
    dry_run = bool(payload.get("dry_run"))
    start_after_filename = str(payload.get("start_after_filename") or "")

    throttler = _gallery_job_progress_throttler(job_id)

    def progress(updates: dict):
        last_filename = str(updates.pop("last_filename", "") or "")
        if full_reconcile and last_filename:
            payload["start_after_filename"] = last_filename
            updates["payload"] = payload
        force = updates.get("stage") in {"preparing", "listing_remote", "completed"}
        throttler.emit(updates, force=force)

    try:
        r2_settings = await asyncio.to_thread(load_r2_backup_settings)
        effective = await asyncio.to_thread(
            r2_config.resolve_r2_backup_settings,
            r2_settings,
            require_enabled=True,
        )
        total_count = await asyncio.to_thread(
            count_gallery_r2_sync_rows,
            key_prefix=effective.key_prefix,
            full_reconcile=full_reconcile,
            start_after_filename=start_after_filename,
        )
        await _publish_gallery_job(
            job_id,
            {
                "status": "running",
                "stage": "preparing",
                "message": "Preparing R2 gallery sync dry run" if dry_run else "Preparing R2 gallery sync",
                "progress": 0,
                "total_count": total_count,
                "lease_expires_at": _gallery_job_lease_expires_at(),
            },
        )
        result = await asyncio.to_thread(
            _call_gallery_r2_sync,
            r2_settings,
            iter_gallery_r2_sync_rows(
                key_prefix=effective.key_prefix,
                full_reconcile=full_reconcile,
                start_after_filename=start_after_filename,
            ),
            total_count=total_count,
            progress_cb=progress,
            state_recorder=None if dry_run else mark_gallery_r2_sync_state,
            full_reconcile=full_reconcile,
            dry_run=dry_run,
            concurrency=config.R2_SYNC_CONCURRENCY,
        )
        payload.pop("start_after_filename", None)
        await _publish_gallery_job(
            job_id,
            {
                "status": "success",
                "stage": "completed",
                "message": "R2 gallery sync dry run complete" if dry_run else "R2 gallery sync complete",
                "progress": 100,
                "error": None,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                "payload": payload,
                **result.to_updates(),
            },
        )
    except asyncio.CancelledError:
        raise
    except r2_config.R2SyncError as e:
        logger.warning("Gallery R2 sync job %s finished with upload errors", job_id)
        await _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "R2 gallery sync failed",
                "progress": 100,
                "error": str(e),
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                **e.result.to_updates(),
            },
        )
    except Exception as e:
        logger.warning("Gallery R2 sync job %s failed", job_id, exc_info=True)
        await _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "R2 gallery sync failed",
                "error": str(e),
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
