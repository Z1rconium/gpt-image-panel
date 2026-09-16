"""Payload shaping for gallery job status responses and SSE events.

Pure functions over stored job rows: the API layer wraps their output in
the response models, and the SSE feeds publish it as event data."""

from collections.abc import Iterable, Iterator
from urllib.parse import quote


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
from ..schemas.nodeimage import NodeImageBatchUploadItem
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


def _missing_gallery_ids(requested_ids: list[str], entries: list[GalleryEntry]) -> list[str]:
    found_ids = {entry.id for entry in entries}
    return [image_id for image_id in requested_ids if image_id not in found_ids]


def _gallery_export_payload(job: dict) -> dict:
    keys = (
        "job_id",
        "status",
        "stage",
        "message",
        "progress",
        "filename",
        "download_url",
        "requested_count",
        "processed_count",
        "exported_count",
        "missing_count",
        "bytes_total",
        "bytes_written",
        "created_at",
        "updated_at",
        "error",
    )
    return {key: job.get(key) for key in keys}


def _gallery_sync_payload(job: dict) -> dict:
    payload = job.get("payload") or {}
    keys = (
        "job_id",
        "status",
        "stage",
        "message",
        "progress",
        "created_at",
        "updated_at",
        "error",
        "total_count",
        "compared_count",
        "uploaded_count",
        "pending_upload_count",
        "skipped_existing_count",
        "missing_local_count",
        "failed_count",
        "bytes_total",
        "bytes_uploaded",
    )
    data = {key: job.get(key) for key in keys}
    data["dry_run"] = bool(payload.get("dry_run"))
    data["checkpoint_filename"] = str(payload.get("start_after_filename") or "") or None
    return data


def _gallery_import_payload(job: dict) -> dict:
    payload = {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "message": job.get("message"),
        "progress": job.get("progress"),
        "requested_count": job.get("requested_count") or 0,
        "processed_count": job.get("processed_count") or 0,
        "imported_count": job.get("exported_count") or 0,
        "skipped_count": job.get("missing_count") or 0,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
    }
    return payload


def _nodeimage_result_item(
    image_id: str,
    filename: str | None,
    status: str,
    *,
    url: str | None = None,
    markdown: str | None = None,
    error: str | None = None,
) -> dict:
    return NodeImageBatchUploadItem(
        image_id=str(image_id),
        filename=filename,
        status=status,
        url=url,
        markdown=markdown,
        error=error,
    ).model_dump()


def _nodeimage_result_counts(results: Iterable[dict]) -> tuple[int, int, int, int]:
    normalized = list(results)
    uploaded_count = sum(item.get("status") == "ok" for item in normalized)
    failed_count = sum(item.get("status") == "error" for item in normalized)
    cancelled_count = sum(item.get("status") == "cancelled" for item in normalized)
    return len(normalized), uploaded_count, failed_count, cancelled_count


def _nodeimage_upload_payload(job: dict) -> dict:
    stored_payload = job.get("payload") or {}
    job_id = str(job.get("job_id") or "")
    encoded_job_id = quote(job_id, safe="") if job_id else ""
    ids = [str(value) for value in stored_payload.get("ids") or [] if str(value)]
    results_by_id: dict[str, dict] = {}
    for value in stored_payload.get("results") or []:
        if not isinstance(value, dict):
            continue
        image_id = str(value.get("image_id") or "")
        if image_id and image_id not in results_by_id:
            results_by_id[image_id] = value
    results = [results_by_id[image_id] for image_id in ids if image_id in results_by_id]
    _, uploaded_count, failed_count, cancelled_count = _nodeimage_result_counts(results)
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "stage": job.get("stage"),
        "message": job.get("message"),
        "progress": job.get("progress") or 0,
        "requested_count": job.get("requested_count") or len(ids),
        "processed_count": job.get("processed_count") or len(results),
        "uploaded_count": job.get("uploaded_count") or uploaded_count,
        "failed_count": job.get("failed_count") or failed_count,
        "cancelled_count": cancelled_count,
        "results": results,
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
        "status_url": f"/api/gallery/nodeimage-upload-jobs/{encoded_job_id}" if encoded_job_id else None,
        "events_url": f"/api/gallery/nodeimage-upload-jobs/{encoded_job_id}/events" if encoded_job_id else None,
        "cancel_url": f"/api/gallery/nodeimage-upload-jobs/{encoded_job_id}/cancel" if encoded_job_id else None,
    }


def _gallery_job_event_name(kind: str) -> str:
    if kind == "sync":
        return "sync"
    if kind == "import":
        return "import"
    if kind == "ai_analyze":
        return "analysis"
    if kind == NODEIMAGE_UPLOAD_JOB_KIND:
        return "nodeimage_upload"
    return "export"


def _gallery_ai_analyze_payload(job: dict) -> dict:
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "message": job.get("message"),
        "progress": job.get("progress") or 0,
        "requested_count": job.get("requested_count") or 0,
        "processed_count": job.get("processed_count") or 0,
        "analyzed_count": job.get("exported_count") or 0,
        "missing_count": job.get("missing_count") or 0,
        "failed_count": job.get("failed_count") or 0,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
    }


def _gallery_job_payload(kind: str, job: dict) -> dict:
    if kind == "sync":
        return _gallery_sync_payload(job)
    if kind == "import":
        return _gallery_import_payload(job)
    if kind == "ai_analyze":
        return _gallery_ai_analyze_payload(job)
    if kind == NODEIMAGE_UPLOAD_JOB_KIND:
        return _nodeimage_upload_payload(job)
    return _gallery_export_payload(job)
