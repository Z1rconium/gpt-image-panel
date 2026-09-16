"""Gallery export jobs: archive building, direct-download slots, and the
export runner (including its R2 mirror when backup is configured)."""

from .gallery_job_shared import (
    _direct_export_slot_expires_at,
    _gallery_job_lease_expires_at,
    _publish_gallery_job,
    _publish_gallery_job_from_worker,
    _publish_gallery_job_progress_from_worker,
)

import asyncio
import logging
import os
from collections.abc import Iterable
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path
from urllib.parse import quote

from .gallery_job_payloads import _missing_gallery_ids

from fastapi.responses import StreamingResponse

from ..core.errors import RateLimitedError
from ..runtime.state import state
from .gallery_archive_export import (
    iter_gallery_zip_chunks,
    prepare_gallery_zip_chunks,
    write_gallery_zip_file,
)
from .gallery_archive_shared import GalleryZipFileResult
from ..runtime.blocking import run_db_operation
from ..core import settings as config
from ..core.utils import utc_now
from ..repositories.coordination import (
    count_active_gallery_jobs,
    delete_gallery_job,
    reserve_gallery_job_capacity,
    update_gallery_job,
)
from ..repositories.gallery.queries import (
    get_gallery_count,
    get_gallery_entries_by_ids,
    iter_gallery_export_rows,
)
from ..schemas.gallery import GalleryEntry
from .gallery_common import (
    GALLERY_EXPORT_TERMINAL_STATUSES,
    MAX_ACTIVE_EXPORT_JOBS,
    PRIVATE_GALLERY_CACHE_CONTROL,
    GalleryProgressThrottler,
    _resolve_trusted_gallery_job_path,
    _unlink_trusted_gallery_job_path,
)
logger = logging.getLogger(__name__)


async def _gallery_zip_response(
    entries,
    filename_prefix: str,
    skipped: list[dict] | None = None,
    extra_headers: dict[str, str] | None = None,
    reserve_export_slot: bool = False,
    direct_export_job: dict | None = None,
    requested_count: int = 0,
    prepare_before_response: bool = False,
) -> StreamingResponse:
    cleanup_direct_job = False
    if direct_export_job:
        active_direct_job = direct_export_job
    elif reserve_export_slot:
        active_direct_job = await _reserve_gallery_export_direct_slot(
            filename_prefix=filename_prefix,
            requested_count=requested_count,
            stage="streaming",
            message="Streaming direct gallery ZIP download",
        )
        cleanup_direct_job = True
    else:
        active_direct_job = None

    direct_job_id = str(active_direct_job.get("job_id")) if active_direct_job else None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = str(active_direct_job.get("filename") or "") if active_direct_job else ""
    if not filename:
        filename = f"{filename_prefix}-{timestamp}.zip"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Encoding": "identity",
        "Cache-Control": PRIVATE_GALLERY_CACHE_CONTROL,
        "X-Content-Type-Options": "nosniff",
    }
    if direct_job_id:
        headers["X-Gallery-Export-Job-Id"] = direct_job_id
    if extra_headers:
        headers.update(extra_headers)

    def progress(updates: dict):
        if not direct_job_id or not throttler:
            return
        force = updates.get("stage") in {"preparing", "streaming"} and (
            updates.get("progress") in {0, 20, 100}
            or updates.get("status") in GALLERY_EXPORT_TERMINAL_STATUSES
        )
        throttler.emit(
            {
                **updates,
                "filename": filename,
                "download_url": f"/api/download-all?export_job_id={quote(direct_job_id)}",
                "lease_expires_at": _direct_export_slot_expires_at(),
            },
            force=force,
        )

    async def mark_direct_job(updates: dict) -> None:
        if direct_job_id:
            await run_db_operation(
                update_gallery_job,
                direct_job_id,
                updates,
                metric_name="update_gallery_job",
            )

    throttler = GalleryProgressThrottler(
        lambda updates: _publish_gallery_job_progress_from_worker(direct_job_id, updates)
    ) if direct_job_id else None

    prepared_chunks = None
    prepared_result: GalleryZipFileResult | None = None
    if prepare_before_response:
        try:
            if direct_job_id:
                await mark_direct_job(
                    {
                        "status": "running",
                        "stage": "preparing",
                        "message": "Preparing gallery ZIP entries",
                        "progress": 0,
                        "filename": filename,
                        "download_url": f"/api/download-all?export_job_id={quote(direct_job_id)}",
                        "requested_count": requested_count,
                        "started_at": utc_now(),
                        "lease_expires_at": _direct_export_slot_expires_at(),
                        "error": None,
                    }
                )
            prepared_chunks, prepared_result = await asyncio.to_thread(
                prepare_gallery_zip_chunks,
                entries,
                skipped=skipped,
                requested_count=requested_count,
                progress=progress if direct_job_id else None,
            )
            headers.setdefault("X-Gallery-Requested-Count", str(prepared_result.requested_count))
            headers.setdefault("X-Gallery-Exported-Count", str(prepared_result.exported_count))
            headers.setdefault("X-Gallery-Missing-Count", str(prepared_result.missing_count))
        except Exception as e:
            if direct_job_id and not cleanup_direct_job:
                await mark_direct_job(
                    {
                        "status": "error",
                        "stage": "error",
                        "message": "Failed to prepare ZIP archive",
                        "error": str(e),
                        "completed_at": utc_now(),
                        "lease_owner": None,
                        "lease_expires_at": None,
                    }
                )
            if cleanup_direct_job and direct_job_id:
                _release_gallery_export_direct_slot(direct_job_id)
            raise

    def zip_chunks():
        try:
            if direct_job_id and prepared_result is None:
                _publish_gallery_job_from_worker(
                    direct_job_id,
                    {
                        "status": "running",
                        "stage": "preparing",
                        "message": "Preparing gallery ZIP entries",
                        "progress": 0,
                        "filename": filename,
                        "download_url": f"/api/download-all?export_job_id={quote(direct_job_id)}",
                        "requested_count": requested_count,
                        "started_at": utc_now(),
                        "lease_expires_at": _direct_export_slot_expires_at(),
                        "error": None,
                    }
                )
            if prepared_result is not None and prepared_chunks is not None:
                yield from prepared_chunks
                result = prepared_result
            else:
                result = yield from iter_gallery_zip_chunks(
                    entries,
                    skipped=skipped,
                    requested_count=requested_count,
                    progress=progress if direct_job_id else None,
                )
            if direct_job_id:
                _publish_gallery_job_from_worker(
                    direct_job_id,
                    {
                        "status": "success",
                        "stage": "ready",
                        "message": "ZIP archive streamed",
                        "progress": 100,
                        "processed_count": result.requested_count,
                        "requested_count": result.requested_count,
                        "exported_count": result.exported_count,
                        "missing_count": result.missing_count,
                        "bytes_total": result.bytes_total,
                        "bytes_written": result.bytes_total,
                        "completed_at": utc_now(),
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "error": None,
                    }
                )
        except (GeneratorExit, asyncio.CancelledError):
            if direct_job_id and not cleanup_direct_job:
                _publish_gallery_job_from_worker(
                    direct_job_id,
                    {
                        "status": "error",
                        "stage": "error",
                        "message": "Direct ZIP download interrupted",
                        "error": "Client disconnected before ZIP streaming completed",
                        "completed_at": utc_now(),
                        "lease_owner": None,
                        "lease_expires_at": None,
                    }
                )
            raise
        except Exception as e:
            if direct_job_id and not cleanup_direct_job:
                _publish_gallery_job_from_worker(
                    direct_job_id,
                    {
                        "status": "error",
                        "stage": "error",
                        "message": "Failed to stream ZIP archive",
                        "error": str(e),
                        "completed_at": utc_now(),
                        "lease_owner": None,
                        "lease_expires_at": None,
                    }
                )
            raise
        finally:
            if cleanup_direct_job and direct_job_id:
                _release_gallery_export_direct_slot(direct_job_id)

    return StreamingResponse(
        zip_chunks(),
        media_type="application/zip",
        headers=headers,
    )


def _gallery_export_lock() -> asyncio.Lock:
    if not hasattr(state, "gallery_export_lock"):
        state.gallery_export_lock = asyncio.Lock()
    return state.gallery_export_lock


def _create_gallery_export_direct_slot(
    *,
    filename_prefix: str = "gpt-images",
    requested_count: int = 0,
    status: str = "running",
    stage: str = "queued",
    message: str = "Waiting for direct gallery ZIP download",
    payload: dict | None = None,
) -> dict:
    job_id = f"direct-{os.urandom(16).hex()}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}-{timestamp}.zip"
    now = utc_now()
    return {
        "job_id": job_id,
        "kind": "export_direct",
        "status": status,
        "stage": stage,
        "message": message,
        "progress": 0,
        "filename": filename,
        "download_url": f"/api/download-all?export_job_id={quote(job_id)}",
        "requested_count": requested_count,
        "processed_count": 0,
        "exported_count": 0,
        "missing_count": 0,
        "bytes_total": 0,
        "bytes_written": 0,
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "lease_expires_at": _direct_export_slot_expires_at(),
        "payload": payload or {},
    }


def _release_gallery_export_direct_slot(job_id: str | None) -> None:
    if job_id:
        delete_gallery_job("export_direct", job_id)


async def _reserve_gallery_export_direct_slot(
    *,
    filename_prefix: str = "gpt-images",
    requested_count: int = 0,
    stage: str = "queued",
    message: str = "Waiting for direct gallery ZIP download",
    payload: dict | None = None,
) -> dict:
    async with _gallery_export_lock():
        slot = await asyncio.to_thread(
            reserve_gallery_job_capacity,
            job=_create_gallery_export_direct_slot(
                filename_prefix=filename_prefix,
                requested_count=requested_count,
                stage=stage,
                message=message,
                payload=payload,
            ),
            counted_kinds=("export", "export_direct"),
            max_active=MAX_ACTIVE_EXPORT_JOBS,
        )
        if not slot:
            active_count = await asyncio.to_thread(count_active_gallery_jobs, "export")
            active_count += await asyncio.to_thread(
                count_active_gallery_jobs,
                "export_direct",
            )
            raise RateLimitedError(f"Too many active export jobs ({active_count}). Please wait for existing exports to complete.")
        return slot


def _build_export_job_entries(job: dict) -> tuple[Iterable[GalleryEntry | dict], int, list[dict]]:
    payload = job.get("payload") or {}
    filters = payload.get("filters")
    if isinstance(filters, dict):
        requested_count = get_gallery_count(filters)
        return iter_gallery_export_rows(filters), requested_count, []
    ids = payload.get("ids")
    if ids:
        entries = get_gallery_entries_by_ids(ids)
        missing_ids = _missing_gallery_ids(ids, entries)
        skipped = [
            {
                "id": image_id,
                "reason": "gallery_entry_missing",
            }
            for image_id in missing_ids
        ]
        return entries, len(ids), skipped
    requested_count = get_gallery_count()
    return iter_gallery_export_rows(), requested_count, []


def _build_gallery_export_job(
    filename_prefix: str,
    requested_count: int,
    payload: dict,
) -> dict:
    job_id = payload.get("job_id") or os.urandom(16).hex()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}-{timestamp}.zip"
    path = Path(config.DATA_DIR) / "exports" / f"{job_id}.zip"
    now = utc_now()
    return {
        "job_id": job_id,
        "kind": "export",
        "status": "queued",
        "stage": "queued",
        "message": "Queued gallery ZIP export",
        "progress": 0,
        "filename": filename,
        "download_url": None,
        "requested_count": requested_count,
        "processed_count": 0,
        "exported_count": 0,
        "missing_count": 0,
        "bytes_total": 0,
        "bytes_written": 0,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "path": str(path),
        "payload": payload,
    }


async def _create_reserved_gallery_export_job(
    filename_prefix: str,
    requested_count: int,
    payload: dict,
) -> dict:
    async with _gallery_export_lock():
        job = await asyncio.to_thread(
            reserve_gallery_job_capacity,
            job=_build_gallery_export_job(filename_prefix, requested_count, payload),
            counted_kinds=("export", "export_direct"),
            max_active=MAX_ACTIVE_EXPORT_JOBS,
        )
        if not job:
            active_count = await asyncio.to_thread(count_active_gallery_jobs, "export")
            active_count += await asyncio.to_thread(
                count_active_gallery_jobs,
                "export_direct",
            )
            raise RateLimitedError(f"Too many active export jobs ({active_count}). Please wait for existing exports to complete.")
        return job


async def _run_gallery_export_job(job: dict) -> None:
    job_id = job["job_id"]
    export_path = _resolve_trusted_gallery_job_path(job.get("path"), kind="export")

    def publish_progress(updates: dict):
        updates = {**updates, "lease_expires_at": _gallery_job_lease_expires_at()}
        _publish_gallery_job_progress_from_worker(job_id, updates)

    throttler = GalleryProgressThrottler(publish_progress)

    def progress(updates: dict):
        force = updates.get("stage") in {"preparing", "packing"} and (
            updates.get("progress") in {0, 20, 100}
            or updates.get("status") in GALLERY_EXPORT_TERMINAL_STATUSES
        )
        throttler.emit(updates, force=force)

    try:
        if not export_path:
            raise ValueError("Export archive path is invalid")
        entries, requested_count, skipped = await asyncio.to_thread(_build_export_job_entries, job)
        await _publish_gallery_job(
            job_id,
            {
                "status": "running",
                "stage": "preparing",
                "message": "Preparing gallery ZIP entries",
                "progress": 0,
                "requested_count": requested_count,
                "lease_expires_at": _gallery_job_lease_expires_at(),
            },
        )
        result: GalleryZipFileResult = await asyncio.to_thread(
            write_gallery_zip_file,
            entries,
            export_path,
            requested_count=requested_count,
            skipped=skipped,
            progress=progress,
        )
        await _publish_gallery_job(
            job_id,
            {
                "status": "success",
                "stage": "ready",
                "message": "ZIP archive ready",
                "progress": 100,
                "processed_count": result.requested_count,
                "requested_count": result.requested_count,
                "exported_count": result.exported_count,
                "missing_count": result.missing_count,
                "bytes_total": result.bytes_total,
                "bytes_written": result.bytes_total,
                "download_url": f"/api/gallery/export-jobs/{job_id}/download",
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                "error": None,
            },
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Failed to build gallery export ZIP job %s", job_id, exc_info=True)
        _unlink_trusted_gallery_job_path(job.get("path"), kind="export", job_id=job_id)
        await _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "Failed to build ZIP archive",
                "error": str(e),
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
