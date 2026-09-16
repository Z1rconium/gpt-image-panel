import asyncio
import inspect
import logging
import os
import time
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .gallery_job_payloads import (
    _missing_gallery_ids,
    _nodeimage_result_counts,
    _nodeimage_result_item,
)
from .gallery_job_sse import (
    _publish_gallery_job_sse,
)

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from ..runtime.state import state
from .gallery_archive_export import (
    iter_gallery_zip_chunks,
    prepare_gallery_zip_chunks,
    write_gallery_zip_file,
)
from .gallery_archive_import import (
    count_import_gallery_entries,
    iter_import_gallery_entries,
    stream_upload_to_tempfile,
)
from .gallery_archive_shared import (
    GalleryZipFileResult,
    import_archive_max_bytes,
)
from ..runtime.blocking import run_db_operation, run_db_operation_in_current_thread
from .job_queue import kick_thumbnail_dispatcher
from ..core import settings as config
from ..core.observability import metrics
from ..core.utils import utc_now
from ..runtime.state import utc_lease_expires_at
from ..integrations.r2 import config as r2_config
from ..integrations.r2 import sync as r2_algorithm
from ..repositories.coordination import (
    acquire_background_lease,
    claim_next_gallery_job,
    cleanup_expired_gallery_jobs,
    cleanup_stale_gallery_jobs,
    count_active_gallery_jobs,
    create_gallery_job,
    delete_gallery_job,
    get_gallery_job,
    get_gallery_jobs_updated_at_edges,
    has_claimable_gallery_job,
    list_gallery_job_ids_with_files,
    release_background_lease,
    release_import_upload_reservation,
    reserve_gallery_job_capacity,
    renew_gallery_job_lease,
    update_gallery_job,
    update_gallery_job_progress,
)
from ..repositories.gallery.mutations import (
    cleanup_orphan_gallery_files,
    delete_all_gallery_images,
    delete_gallery_image,
    delete_gallery_images,
    delete_gallery_images_by_filters,
    import_gallery_entries,
    invalidate_thumbnail_cache,
    is_gallery_filename_referenced,
    update_gallery_entries_favorite,
    update_gallery_entries_favorite_by_filters,
    update_gallery_entry,
)
from ..repositories.gallery.queries import (
    get_gallery_count,
    get_gallery_entries_by_ids,
    get_gallery_entry,
    get_gallery_ids,
    get_gallery_page,
    iter_gallery_export_rows,
)
from ..repositories.gallery.sync_state import (
    count_gallery_r2_sync_rows,
    iter_gallery_r2_sync_rows,
    mark_gallery_r2_sync_state,
)
from ..core.media import (
    THUMBNAIL_CONTENT_TYPE,
    safe_image_path,
    safe_thumbnail_path,
)
from ..repositories.settings import load_nodeimage_settings, load_r2_backup_settings
from ..schemas.gallery import (
    GalleryBatchFavoriteRequest,
    GalleryBatchRequest,
    GalleryBatchResponse,
    GalleryEntry,
    GalleryExportJobStatus,
    GalleryExportRequest,
    GalleryFavoriteRequest,
    GalleryImportJobStatus,
    GalleryResponse,
    GallerySelectionTokenRequest,
    GallerySelectionTokenResponse,
    GallerySyncRequest,
    GallerySyncJobStatus,
)
from ..integrations.nodeimage.client import (
    NodeImageAuthError,
    NodeImageConfigurationError,
    NodeImageUploadError,
    NodeImageUploadResult,
    resolve_nodeimage_settings,
    upload_image_file,
)
from .claim_loop import fail_open_precheck, run_claim_loop
from .gallery_common import (
    BACKGROUND_TASK_ERROR_BACKOFF_INITIAL_SECONDS,
    BACKGROUND_TASK_ERROR_BACKOFF_MAX_SECONDS,
    BACKGROUND_TASK_LEASE_SECONDS,
    DIRECT_EXPORT_SLOT_LEASE_SECONDS,
    GALLERY_EXPORT_TERMINAL_STATUSES,
    GALLERY_IMPORT_TERMINAL_STATUSES,
    GALLERY_JOB_DISPATCH_INTERVAL_SECONDS,
    GALLERY_JOB_DISPATCH_MAX_IDLE_BACKOFF_SECONDS,
    GALLERY_JOB_LEASE_SECONDS,
    GALLERY_JOB_SSE_IDLE_CHECK_SECONDS,
    GALLERY_JOB_SSE_QUEUE_MAXSIZE,
    MAX_ACTIVE_EXPORT_JOBS,
    MAX_ACTIVE_IMPORT_JOBS,
    MAX_ACTIVE_NODEIMAGE_UPLOAD_JOBS,
    MAX_ACTIVE_SYNC_JOBS,
    NODEIMAGE_UPLOAD_JOB_KIND,
    NODEIMAGE_UPLOAD_JOB_TTL_SECONDS,
    NODEIMAGE_UPLOAD_TERMINAL_STATUSES,
    PRIVATE_GALLERY_CACHE_CONTROL,
    GalleryProgressThrottler,
    _progress_item_count,
    _resolve_trusted_gallery_job_path,
    _unlink_trusted_gallery_job_path,
)

logger = logging.getLogger(__name__)

NODEIMAGE_UPLOAD_SEMAPHORE = asyncio.Semaphore(
    max(1, int(getattr(config, "NODEIMAGE_UPLOAD_CONCURRENCY", 4) or 1))
)
_NODEIMAGE_UPLOAD_SEMAPHORE_LOOP: asyncio.AbstractEventLoop | None = None
_NODEIMAGE_UPLOAD_SEMAPHORE_CAPACITY = max(
    1,
    int(getattr(config, "NODEIMAGE_UPLOAD_CONCURRENCY", 4) or 1),
)

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
            raise HTTPException(
                status_code=429,
                detail=f"Too many active export jobs ({active_count}). "
                "Please wait for existing exports to complete.",
            )
        return slot
































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


def _create_gallery_sync_job(total_count: int, payload: dict | None = None) -> dict:
    job_id = os.urandom(16).hex()
    now = utc_now()
    return create_gallery_job(
        job_id=job_id,
        kind="sync",
        status="queued",
        stage="queued",
        message="Queued R2 gallery sync",
        progress=0,
        created_at=now,
        updated_at=now,
        error=None,
        total_count=total_count,
        compared_count=0,
        uploaded_count=0,
        pending_upload_count=0,
        skipped_existing_count=0,
        missing_local_count=0,
        failed_count=0,
        bytes_total=0,
        bytes_uploaded=0,
        payload=payload or {},
    )


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


def _get_nodeimage_upload_semaphore() -> asyncio.Semaphore:
    global NODEIMAGE_UPLOAD_SEMAPHORE
    global _NODEIMAGE_UPLOAD_SEMAPHORE_CAPACITY, _NODEIMAGE_UPLOAD_SEMAPHORE_LOOP
    current_loop = asyncio.get_running_loop()
    capacity = max(1, int(getattr(config, "NODEIMAGE_UPLOAD_CONCURRENCY", 4) or 1))
    if (
        capacity != _NODEIMAGE_UPLOAD_SEMAPHORE_CAPACITY
        or _NODEIMAGE_UPLOAD_SEMAPHORE_LOOP is not current_loop
    ):
        NODEIMAGE_UPLOAD_SEMAPHORE = asyncio.Semaphore(capacity)
        _NODEIMAGE_UPLOAD_SEMAPHORE_CAPACITY = capacity
        _NODEIMAGE_UPLOAD_SEMAPHORE_LOOP = current_loop
    return NODEIMAGE_UPLOAD_SEMAPHORE


def _build_nodeimage_upload_job(
    ids: list[str],
    requested_count: int,
    missing_ids: list[str],
) -> dict:
    job_id = os.urandom(16).hex()
    now = utc_now()
    initial_results = [
        _nodeimage_result_item(
            image_id,
            None,
            "error",
            error="Gallery entry not found",
        )
        for image_id in missing_ids
    ]
    processed_count, _uploaded_count, failed_count, _cancelled_count = _nodeimage_result_counts(initial_results)
    return {
        "job_id": job_id,
        "kind": NODEIMAGE_UPLOAD_JOB_KIND,
        "status": "queued",
        "stage": "queued",
        "message": "Queued NodeImage upload",
        "progress": round((processed_count / max(1, requested_count)) * 100),
        "requested_count": requested_count,
        "processed_count": processed_count,
        "uploaded_count": 0,
        "failed_count": failed_count,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "payload": {
            "ids": list(ids),
            "results": initial_results,
            "cancel_requested": False,
        },
    }


async def _create_reserved_nodeimage_upload_job(
    ids: list[str],
    requested_count: int,
    missing_ids: list[str],
) -> dict:
    job = await asyncio.to_thread(
        reserve_gallery_job_capacity,
        job=_build_nodeimage_upload_job(ids, requested_count, missing_ids),
        counted_kinds=(NODEIMAGE_UPLOAD_JOB_KIND,),
        max_active=MAX_ACTIVE_NODEIMAGE_UPLOAD_JOBS,
    )
    if not job:
        active_count = await asyncio.to_thread(
            count_active_gallery_jobs,
            NODEIMAGE_UPLOAD_JOB_KIND,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many active NodeImage upload jobs ({active_count}). "
            "Please wait for the existing upload to complete.",
        )
    return job


NodeImageFileInspection = Path | dict


def _inspect_nodeimage_file(entry: GalleryEntry) -> NodeImageFileInspection:
    path = safe_image_path(entry.filename)
    if not path:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file not found",
        )
    try:
        file_size = path.stat().st_size
    except FileNotFoundError:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file not found",
        )
    except OSError:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file could not be read",
        )
    if file_size > config.MAX_FILE_SIZE_MB * 1024 * 1024:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error=f"Image file is too large. Max size is {config.MAX_FILE_SIZE_MB} MB",
        )
    try:
        with path.open("rb"):
            pass
    except FileNotFoundError:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file not found",
        )
    except OSError:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file could not be read",
        )
    if file_size <= 0:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file is empty",
        )
    return path


async def _nodeimage_job_cancel_requested(job_id: str) -> bool:
    current = await asyncio.to_thread(get_gallery_job, NODEIMAGE_UPLOAD_JOB_KIND, job_id)
    if not current:
        return True
    return bool((current.get("payload") or {}).get("cancel_requested"))


def _nodeimage_cancelled_item(entry: GalleryEntry) -> dict:
    return _nodeimage_result_item(
        entry.id,
        entry.filename,
        "cancelled",
        error="Cancelled before upload",
    )


async def _upload_nodeimage_entry(
    job_id: str,
    entry: GalleryEntry,
    effective,
    auth_failed: asyncio.Event,
    *,
    inspected_path: Path | None = None,
) -> dict:
    semaphore = _get_nodeimage_upload_semaphore()
    async with semaphore:
        if await _nodeimage_job_cancel_requested(job_id):
            return _nodeimage_cancelled_item(entry)

        if auth_failed.is_set():
            inspection = await asyncio.to_thread(_inspect_nodeimage_file, entry)
            if isinstance(inspection, dict):
                return inspection
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error="NodeImage API key was rejected.",
            )

        path = inspected_path
        if path is None:
            inspection = await asyncio.to_thread(_inspect_nodeimage_file, entry)
            if isinstance(inspection, dict):
                return inspection
            path = inspection
        if auth_failed.is_set():
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error="NodeImage API key was rejected.",
            )
        try:
            result: NodeImageUploadResult = await upload_image_file(
                path,
                path.name,
                effective,
            )
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "ok",
                url=result.url,
                markdown=result.markdown,
            )
        except NodeImageAuthError:
            auth_failed.set()
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error="NodeImage API key was rejected.",
            )
        except NodeImageUploadError as exc:
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error=str(exc) or "NodeImage upload failed.",
            )
        except FileNotFoundError:
            return _nodeimage_result_item(entry.id, entry.filename, "error", error="Image file not found")
        except OSError:
            return _nodeimage_result_item(entry.id, entry.filename, "error", error="Image file could not be read")
        except Exception as exc:
            logger.exception(
                "Unexpected NodeImage upload failure for gallery entry %s (%s)",
                entry.id,
                type(exc).__name__,
            )
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error="Unexpected upload failure",
            )


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
            raise HTTPException(
                status_code=429,
                detail=f"Too many active export jobs ({active_count}). "
                "Please wait for existing exports to complete.",
            )
        return job


async def _create_reserved_gallery_sync_job(
    total_count: int,
    payload: dict | None = None,
) -> dict:
    active_count = await asyncio.to_thread(count_active_gallery_jobs, "sync")
    if active_count >= MAX_ACTIVE_SYNC_JOBS:
        raise HTTPException(
            status_code=429,
            detail="A gallery R2 sync job is already queued or running.",
        )
    return await asyncio.to_thread(_create_gallery_sync_job, total_count, payload)


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
        raise HTTPException(
            status_code=429,
            detail="A gallery import job is already queued or running.",
        )
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


async def _run_gallery_sync_job(job: dict) -> None:
    job_id = job["job_id"]
    payload = job.get("payload") or {}
    full_reconcile = bool(payload.get("full_reconcile"))
    dry_run = bool(payload.get("dry_run"))
    start_after_filename = str(payload.get("start_after_filename") or "")

    def publish_progress(updates: dict):
        updates = {**updates, "lease_expires_at": _gallery_job_lease_expires_at()}
        _publish_gallery_job_progress_from_worker(job_id, updates)

    throttler = GalleryProgressThrottler(publish_progress)

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

    def publish_progress(updates: dict):
        updates = {**updates, "lease_expires_at": _gallery_job_lease_expires_at()}
        _publish_gallery_job_progress_from_worker(job_id, updates)

    throttler = GalleryProgressThrottler(publish_progress)

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
    except HTTPException as e:
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


class _NodeImageLeaseLost(RuntimeError):
    pass


async def _publish_nodeimage_job(
    job_id: str,
    lease_owner: str,
    updates: dict,
) -> dict:
    job = await run_db_operation(
        update_gallery_job,
        job_id,
        updates,
        lease_owner=lease_owner,
        metric_name="update_nodeimage_upload_job",
    )
    if not job:
        raise _NodeImageLeaseLost()
    _publish_gallery_job_sse(job)
    return job


async def _run_nodeimage_upload_job(job: dict) -> None:
    job_id = str(job["job_id"])
    lease_owner = str(job.get("lease_owner") or "")
    stored_payload = job.get("payload") or {}
    ids = [str(value) for value in stored_payload.get("ids") or [] if str(value)]
    payload = dict(stored_payload)
    results_by_id: dict[str, dict] = {}
    for value in payload.get("results") or []:
        if not isinstance(value, dict):
            continue
        image_id = str(value.get("image_id") or "")
        if image_id and image_id not in results_by_id:
            results_by_id[image_id] = value
    payload["ids"] = ids
    requested_count = max(int(job.get("requested_count") or 0), len(ids))
    auth_failed = asyncio.Event()
    lease_lost = asyncio.Event()
    stop_lease_renewal = asyncio.Event()

    async def renew_lease() -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        stop_lease_renewal.wait(),
                        timeout=max(1.0, GALLERY_JOB_LEASE_SECONDS / 3),
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                renewed = await asyncio.to_thread(
                    renew_gallery_job_lease,
                    job_id=job_id,
                    lease_owner=lease_owner,
                    lease_expires_at=_gallery_job_lease_expires_at(),
                    now=utc_now(),
                )
                if not renewed:
                    lease_lost.set()
                    return
        except asyncio.CancelledError:
            raise

    renew_task = asyncio.create_task(renew_lease())

    def ordered_results() -> list[dict]:
        return [results_by_id[image_id] for image_id in ids if image_id in results_by_id]

    async def persist_results(*, status: str = "running", stage: str = "uploading", message: str = "Uploading images to NodeImage") -> None:
        results = ordered_results()
        processed_count, uploaded_count, failed_count, _cancelled_count = _nodeimage_result_counts(results)
        payload["results"] = results
        if lease_lost.is_set():
            raise _NodeImageLeaseLost()
        await _publish_nodeimage_job(
            job_id,
            lease_owner,
            {
                "status": status,
                "stage": stage,
                "message": message,
                "progress": 100 if status in NODEIMAGE_UPLOAD_TERMINAL_STATUSES else round(
                    (processed_count / max(1, requested_count)) * 100
                ),
                "requested_count": requested_count,
                "processed_count": processed_count,
                "uploaded_count": uploaded_count,
                "failed_count": failed_count,
                "payload": payload,
                "error": None,
            },
        )

    async def persist_item(item: dict) -> None:
        results_by_id[str(item["image_id"])] = item
        await persist_results()

    try:
        effective = resolve_nodeimage_settings(
            await asyncio.to_thread(load_nodeimage_settings)
        )
        entries = await asyncio.to_thread(get_gallery_entries_by_ids, ids)
        entries_by_id = {entry.id: entry for entry in entries}
        for image_id in ids:
            if image_id in results_by_id or image_id in entries_by_id:
                continue
            results_by_id[image_id] = _nodeimage_result_item(
                image_id,
                None,
                "error",
                error="Gallery entry not found",
            )

        pending_entries = [
            entries_by_id[image_id]
            for image_id in ids
            if image_id in entries_by_id and image_id not in results_by_id
        ]
        await persist_results(message="Preparing NodeImage upload")

        while pending_entries and not await _nodeimage_job_cancel_requested(job_id):
            probe_entry = pending_entries.pop(0)
            probe_inspection = await asyncio.to_thread(
                _inspect_nodeimage_file,
                probe_entry,
            )
            if isinstance(probe_inspection, dict):
                await persist_item(probe_inspection)
                continue
            await persist_item(
                await _upload_nodeimage_entry(
                    job_id,
                    probe_entry,
                    effective,
                    auth_failed,
                    inspected_path=probe_inspection,
                )
            )
            break

        if pending_entries and not await _nodeimage_job_cancel_requested(job_id):
            tasks = [
                asyncio.create_task(
                    _upload_nodeimage_entry(
                        job_id,
                        entry,
                        effective,
                        auth_failed,
                    )
                )
                for entry in pending_entries
            ]
            try:
                for task in asyncio.as_completed(tasks):
                    await persist_item(await task)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        if await _nodeimage_job_cancel_requested(job_id):
            for entry in pending_entries:
                if entry.id not in results_by_id:
                    results_by_id[entry.id] = _nodeimage_cancelled_item(entry)
        results = ordered_results()
        _processed_count, uploaded_count, failed_count, cancelled_count = _nodeimage_result_counts(results)
        if cancelled_count:
            terminal_status = "cancelled"
            terminal_message = "NodeImage upload cancelled"
        elif failed_count:
            terminal_status = "partial_failure"
            terminal_message = "NodeImage upload completed with failures"
        else:
            terminal_status = "success"
            terminal_message = "NodeImage upload complete"
        payload["results"] = results
        await _publish_nodeimage_job(
            job_id,
            lease_owner,
            {
                "status": terminal_status,
                "stage": "completed" if terminal_status != "cancelled" else "cancelled",
                "message": terminal_message,
                "progress": 100,
                "requested_count": requested_count,
                "processed_count": len(results),
                "uploaded_count": uploaded_count,
                "failed_count": failed_count,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                "payload": payload,
                "error": None,
            },
        )
    except _NodeImageLeaseLost:
        logger.info("NodeImage upload job %s stopped after losing its lease", job_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("NodeImage upload job %s failed", job_id)
        try:
            results = ordered_results()
            remaining_error = "NodeImage upload job failed"
            entries = await asyncio.to_thread(get_gallery_entries_by_ids, ids)
            for entry in entries:
                if entry.id not in results_by_id:
                    results_by_id[entry.id] = _nodeimage_result_item(
                        entry.id,
                        entry.filename,
                        "error",
                        error=remaining_error,
                    )
            payload["results"] = ordered_results()
            _processed_count, uploaded_count, failed_count, _cancelled_count = _nodeimage_result_counts(payload["results"])
            await _publish_nodeimage_job(
                job_id,
                lease_owner,
                {
                    "status": "error",
                    "stage": "error",
                    "message": "NodeImage upload failed",
                    "progress": 100,
                    "requested_count": requested_count,
                    "processed_count": len(payload["results"]),
                    "uploaded_count": uploaded_count,
                    "failed_count": failed_count,
                    "completed_at": utc_now(),
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "payload": payload,
                    "error": remaining_error,
                },
            )
        except _NodeImageLeaseLost:
            logger.info("NodeImage upload job %s stopped after losing its lease", job_id)
    finally:
        stop_lease_renewal.set()
        if not renew_task.done():
            renew_task.cancel()
        await asyncio.gather(renew_task, return_exceptions=True)


async def _run_gallery_job_dispatcher(kind: str, worker_id: str, running_limit: int) -> None:
    runner_by_kind = {
        "export": _run_gallery_export_job,
        "sync": _run_gallery_sync_job,
        "import": _run_gallery_import_job,
        NODEIMAGE_UPLOAD_JOB_KIND: _run_nodeimage_upload_job,
    }
    runner = runner_by_kind[kind]

    async def claim_gallery_job():
        return await asyncio.to_thread(
            claim_next_gallery_job,
            kind=kind,
            worker_id=worker_id,
            lease_expires_at=_gallery_job_lease_expires_at(),
            now=utc_now(),
            running_limit=running_limit,
            counted_kinds=_claim_counted_gallery_kinds(kind),
        )

    async def run_gallery_job(job: dict):
        await runner(job)

    async def has_claimable_gallery() -> bool:
        return await asyncio.to_thread(
            has_claimable_gallery_job,
            kind=kind,
            now=utc_now(),
        )

    await run_claim_loop(
        claim_fn=claim_gallery_job,
        run_fn=run_gallery_job,
        running_limit=running_limit,
        idle_interval=GALLERY_JOB_DISPATCH_INTERVAL_SECONDS,
        max_backoff=GALLERY_JOB_DISPATCH_MAX_IDLE_BACKOFF_SECONDS,
        claim_miss_fn=lambda: metrics.increment(f"gallery.{kind}.claim_miss"),
        claim_precheck_fn=fail_open_precheck(has_claimable_gallery),
        claim_precheck_metric=f"gallery.{kind}.claim_precheck_skipped",
        logger=logger,
        error_message=f"Gallery {kind} dispatcher error",
        task_name=f"gallery {kind} job",
    )


async def run_gallery_export_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("export", worker_id, MAX_ACTIVE_EXPORT_JOBS)


async def run_gallery_sync_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("sync", worker_id, MAX_ACTIVE_SYNC_JOBS)


async def run_gallery_import_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("import", worker_id, MAX_ACTIVE_IMPORT_JOBS)


async def run_gallery_nodeimage_upload_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher(
        NODEIMAGE_UPLOAD_JOB_KIND,
        worker_id,
        MAX_ACTIVE_NODEIMAGE_UPLOAD_JOBS,
    )



__all__ = [name for name in globals() if not name.startswith("__")]
