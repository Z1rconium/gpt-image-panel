"""Gallery import jobs: ingesting an uploaded archive and its metadata."""

from .gallery_job_shared import (
    _gallery_job_lease_expires_at,
    _gallery_job_progress_throttler,
    _publish_gallery_job,
)

import asyncio
import logging
import os
from pathlib import Path



from .gallery_archive_import import iter_import_gallery_entries
from .job_queue import kick_thumbnail_dispatcher
from ..core.utils import utc_now
from ..core.errors import (
    DomainError,
    RateLimitedError,
)
from ..repositories.coordination import (
    release_import_upload_reservation,
    reserve_gallery_job_capacity,
)
from ..repositories.gallery.mutations import import_gallery_entries
from .gallery_common import (
    GALLERY_IMPORT_TERMINAL_STATUSES,
    MAX_ACTIVE_IMPORT_JOBS,
    _resolve_trusted_gallery_job_path,
    _unlink_trusted_gallery_job_path,
)
logger = logging.getLogger(__name__)


def _build_gallery_import_job(
    zip_path: Path,
    total_count: int,
    payload: dict | None = None,
) -> dict:
    job_id = os.urandom(16).hex()
    now = utc_now()
    return {
        "job_id": job_id,
        "kind": "import",
        "status": "queued",
        "stage": "queued",
        "message": "Queued gallery ZIP import",
        "progress": 0,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "path": str(zip_path),
        "requested_count": total_count,
        "processed_count": 0,
        "exported_count": 0,
        "missing_count": 0,
        "payload": payload or {},
    }


async def _create_reserved_gallery_import_job(
    zip_path: Path,
    total_count: int,
    payload: dict | None = None,
) -> dict:
    job = await asyncio.to_thread(
        reserve_gallery_job_capacity,
        job=_build_gallery_import_job(zip_path, total_count, payload),
        counted_kinds=("import",),
        max_active=MAX_ACTIVE_IMPORT_JOBS,
    )
    if not job:
        raise RateLimitedError("A gallery import job is already queued or running.")
    return job


async def _run_gallery_import_job(job: dict) -> None:
    job_id = job["job_id"]
    zip_path = _resolve_trusted_gallery_job_path(job.get("path"), kind="import")
    payload = job.get("payload") or {}
    reservation_id = str(payload.get("reservation_id") or "")
    requested_count = int(job.get("requested_count") or 0)
    last_counts = {
        "processed_count": 0,
        "exported_count": 0,
        "missing_count": 0,
    }

    throttler = _gallery_job_progress_throttler(job_id)

    def progress(updates: dict):
        for key in last_counts:
            if key in updates:
                last_counts[key] = int(updates.get(key) or 0)
        denominator = max(requested_count, last_counts["processed_count"], 1)
        progress_value = min(
            90,
            5 + round((min(last_counts["processed_count"], denominator) / denominator) * 85),
        )
        force = updates.get("stage") in {"validating", "committing"} and (
            updates.get("processed_count") in {0, requested_count}
            or updates.get("status") in GALLERY_IMPORT_TERMINAL_STATUSES
        )
        throttler.emit(
            {
                **updates,
                "progress": progress_value,
                "requested_count": denominator,
            },
            force=force,
        )

    try:
        if not zip_path:
            raise ValueError("Import archive path is invalid")
        if not zip_path.exists():
            raise FileNotFoundError("Import archive file is missing")

        await _publish_gallery_job(
            job_id,
            {
                "status": "running",
                "stage": "validating",
                "message": "Validating import archive entries",
                "progress": 0,
                "requested_count": requested_count,
                "lease_expires_at": _gallery_job_lease_expires_at(),
                "error": None,
            },
        )

        def run_import() -> int:
            return import_gallery_entries(
                iter_import_gallery_entries(zip_path, progress=progress)
            )

        imported_count = await asyncio.to_thread(run_import)
        if imported_count == 0:
            raise ValueError("No importable images found")

        processed_count = max(last_counts["processed_count"], requested_count)
        skipped_count = max(last_counts["missing_count"], processed_count - imported_count)
        await _publish_gallery_job(
            job_id,
            {
                "status": "success",
                "stage": "completed",
                "message": "Gallery import complete",
                "progress": 100,
                "requested_count": max(requested_count, processed_count),
                "processed_count": processed_count,
                "exported_count": imported_count,
                "missing_count": skipped_count,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                "error": None,
            },
        )
        kick_thumbnail_dispatcher()
    except asyncio.CancelledError:
        raise
    except DomainError as e:
        detail = str(e.detail)
        logger.warning("Gallery import job %s failed: %s", job_id, detail)
        await _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "Gallery import failed",
                "progress": 100,
                "error": detail,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
    except Exception as e:
        logger.warning("Gallery import job %s failed", job_id, exc_info=True)
        await _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "Gallery import failed",
                "progress": 100,
                "error": str(e),
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
    finally:
        _unlink_trusted_gallery_job_path(job.get("path"), kind="import", job_id=job_id)
        if reservation_id:
            await asyncio.to_thread(release_import_upload_reservation, reservation_id)
