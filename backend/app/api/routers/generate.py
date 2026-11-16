import hashlib
import hmac
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..responses import streaming_response
from ...services import generate_job_sse
from ...services.job_persistence import resolve_generate_job_view
from ...repositories.db.execution import run_db_operation
from ...runtime.blocking import run_image_operation
from ...services.image_admission import admit_generation
from ...services.job_cancel import cancel_image_job
from ...services.job_queue import enqueue
from ...core import security as auth
from ...core.api_paths import normalize_api_path
from ...core.constants import ACTIVE_GENERATE_JOB_STATUSES, ERROR_GENERATE_JOB_STATUSES
from ...core.media import MASK_CONTENT_TYPE, safe_image_path, safe_mask_path
from ...repositories.gallery.queries import get_gallery_entry
from ...repositories.image_jobs import (
    aggregate_image_job_units,
    clear_generate_job_history as clear_persisted_generate_job_history,
    get_generate_job as get_persisted_generate_job,
    list_generate_jobs as list_persisted_generate_jobs,
)
from ...schemas.common import MessageResponse
from ...schemas.generation import (
    GenerateJobResponse,
    GenerateJobStatus,
    GenerateRequest,
    JobDiagnosticsResponse,
)


router = APIRouter()
MAX_DIAGNOSTIC_UNITS = 20
MAX_DIAGNOSTIC_TASK_ID_CHARS = 64


def _unit_diagnostics_entry(unit: dict) -> dict:
    """Public, bounded per-unit diagnostics view (no raw follow-up URLs)."""
    remote = unit.get("remote") if isinstance(unit.get("remote"), dict) else None
    summary = None
    if remote:
        task_id = remote.get("task_id")
        summary = {
            "phase": str(remote.get("phase") or ""),
            "task_id": str(task_id)[:MAX_DIAGNOSTIC_TASK_ID_CHARS] if task_id else None,
            "submitted_at": remote.get("submitted_at"),
            "deadline_at": remote.get("deadline_at"),
            "has_status_url": bool(remote.get("status_url")),
            "has_result_url": bool(remote.get("result_url")),
            "has_cancel_url": bool(remote.get("cancel_url")),
            "has_idempotency_key": bool(remote.get("idempotency_key")),
        }
    return {
        "unit_id": str(unit.get("unit_id") or ""),
        "unit_index": int(unit.get("unit_index") or 0),
        "status": str(unit.get("status") or ""),
        "stage": unit.get("stage"),
        "message": unit.get("message"),
        "error": unit.get("error"),
        "attempts": int(unit.get("attempts") or 0),
        "recovery_count": int(unit.get("recovery_count") or 0),
        "remote": summary,
        "diagnostics": unit.get("diagnostics"),
    }


@router.post("/api/generate", response_model=GenerateJobResponse, status_code=202)
async def generate(req: GenerateRequest):
    admitted = await admit_generation(
        req,
        api_path_for=lambda preset: normalize_api_path(
            req.api_path or str(preset.get("api_path") or "/v1/images/generations")
        ),
    )
    return await enqueue(admitted, queued_message="Queued image generation")


@router.get("/api/generate/jobs", response_model=list[GenerateJobStatus])
async def list_generate_jobs(
    include_finished: bool = Query(default=False),
    failed_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    before_updated_at: str | None = Query(default=None, max_length=64),
    before_job_id: str | None = Query(default=None, max_length=128),
):
    if include_finished:
        statuses = ERROR_GENERATE_JOB_STATUSES if failed_only else None
        jobs = await run_db_operation(
            list_persisted_generate_jobs,
            statuses=statuses,
            limit=limit,
            offset=offset,
            before_updated_at=before_updated_at,
            before_job_id=before_job_id,
            metric_name="list_generate_jobs",
        )
    else:
        jobs = await run_db_operation(
            list_persisted_generate_jobs,
            statuses=ACTIVE_GENERATE_JOB_STATUSES,
            metric_name="list_active_generate_jobs",
        )
    return [GenerateJobStatus(**job) for job in jobs]


@router.get("/api/generate/jobs/events")
async def stream_generate_jobs(request: Request):
    body = await generate_job_sse.stream_generate_jobs(
        client_ip=auth.get_client_ip(request),
        is_disconnected=request.is_disconnected,
    )
    return streaming_response(body)


@router.delete("/api/generate/jobs/history", response_model=MessageResponse)
async def clear_generate_job_history():
    deleted_count = await run_db_operation(
        clear_persisted_generate_job_history,
        metric_name="clear_generate_job_history",
    )
    return MessageResponse(
        status="success",
        message=f"Deleted {deleted_count} job history entr{'y' if deleted_count == 1 else 'ies'}",
    )


@router.get("/api/generate/{job_id}", response_model=GenerateJobStatus)
async def get_generate_job(job_id: str):
    job = await resolve_generate_job_view(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return GenerateJobStatus(**job)


@router.get("/api/generate/{job_id}/diagnostics", response_model=JobDiagnosticsResponse)
async def get_generate_job_diagnostics(job_id: str):
    job = await run_db_operation(
        get_persisted_generate_job,
        job_id,
        metric_name="get_generate_job_diagnostics",
    )
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    aggregate = await run_db_operation(
        aggregate_image_job_units,
        job_id,
        metric_name="get_generate_job_diagnostics_units",
    )
    entries = [
        _unit_diagnostics_entry(unit)
        for unit in aggregate.get("units") or []
        if unit.get("diagnostics") or unit.get("remote")
    ]
    entries.sort(key=lambda entry: entry["unit_index"])
    return JobDiagnosticsResponse(job_id=job_id, units=entries[:MAX_DIAGNOSTIC_UNITS])


async def _job_mask_coverage(job_id: str) -> float | None:
    aggregate = await run_db_operation(
        aggregate_image_job_units,
        job_id,
        metric_name="get_generate_job_mask_coverage",
    )
    for unit in aggregate.get("units", []):
        for source in unit.get("edit_sources") or []:
            if source.get("role") == "mask":
                coverage = source.get("coverage")
                return float(coverage) if coverage is not None else None
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mask_response(path: Path, coverage: float | None, *, verified: bool, filename: str = "mask.png") -> FileResponse:
    headers = {
        "Cache-Control": (
            "private, no-store" if verified else "private, max-age=31536000, immutable"
        )
    }
    if coverage is not None:
        headers["X-Mask-Coverage"] = str(coverage)
    return FileResponse(path, media_type=MASK_CONTENT_TYPE, filename=filename, headers=headers)


async def _load_job_layer_path(
    job_id: str,
    *,
    flag_key: str,
    filename: str,
    metric_name: str,
    missing_detail: str,
    file_missing_detail: str,
):
    job = await run_db_operation(
        get_persisted_generate_job, job_id, metric_name=metric_name
    )
    if not job or not job.get(flag_key):
        raise HTTPException(status_code=404, detail=missing_detail)
    path = safe_mask_path(filename)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail=file_missing_detail)
    return path


async def _verify_restore_layer_source(
    job_id: str,
    source_sha256: str | None,
    gallery_image_id: str | None,
) -> None:
    if bool(source_sha256) == bool(gallery_image_id):
        raise HTTPException(status_code=400, detail="Provide exactly one edit source identity")
    aggregate = await run_db_operation(
        aggregate_image_job_units, job_id, metric_name="restore_generate_job_sources"
    )
    sources = [
        source
        for unit in aggregate.get("units", [])
        for source in unit.get("edit_sources") or []
    ]
    primary = next(
        (source for source in sources if source.get("role", "image") == "image"),
        None,
    ) or next(
        (source for source in sources if source.get("role") == "reference"),
        None,
    )
    expected = primary.get("raw_sha256") if primary else None
    if not isinstance(expected, str) or len(expected) != 64:
        raise HTTPException(status_code=409, detail="Original edit source identity is unavailable")
    if source_sha256 is not None:
        if (
            primary.get("gallery_image_id")
            or len(source_sha256) != 64
            or any(c not in "0123456789abcdef" for c in source_sha256)
        ):
            raise HTTPException(status_code=409, detail="Edit source does not match this job")
        actual = source_sha256
    else:
        if primary.get("gallery_image_id") != gallery_image_id:
            raise HTTPException(status_code=409, detail="Edit source does not match this job")
        entry = await run_db_operation(
            get_gallery_entry, gallery_image_id, metric_name="restore_generate_gallery_source"
        )
        gallery_path = safe_image_path(entry.filename) if entry else None
        if not gallery_path or not gallery_path.is_file():
            raise HTTPException(status_code=409, detail="Original gallery image is unavailable")
        try:
            actual = await run_image_operation(
                _sha256_file, gallery_path, metric_name="hash_restore_gallery_source"
            )
        except OSError as exc:
            raise HTTPException(status_code=409, detail="Original gallery image is unavailable") from exc
    if not hmac.compare_digest(expected, actual):
        raise HTTPException(status_code=409, detail="Edit source does not match this job")


@router.post("/api/generate/{job_id}/mask/restore")
async def restore_generate_job_mask(
    job_id: str,
    source_sha256: str | None = Form(None),
    gallery_image_id: str | None = Form(None),
):
    path = await _load_job_layer_path(
        job_id,
        flag_key="mask_applied",
        filename=f"{job_id}.png",
        metric_name="restore_generate_job_mask",
        missing_detail="No mask is stored for this job",
        file_missing_detail="Mask file not found",
    )
    await _verify_restore_layer_source(job_id, source_sha256, gallery_image_id)
    return _mask_response(path, await _job_mask_coverage(job_id), verified=True)


@router.get("/api/generate/{job_id}/mask")
async def get_generate_job_mask(job_id: str):
    path = await _load_job_layer_path(
        job_id,
        flag_key="mask_applied",
        filename=f"{job_id}.png",
        metric_name="get_generate_job_mask",
        missing_detail="No mask is stored for this job",
        file_missing_detail="Mask file not found",
    )
    # job_id is unique and the file is never rewritten after promotion, so a
    # retry re-fetching the same job's mask can cache it indefinitely.
    return _mask_response(path, await _job_mask_coverage(job_id), verified=False)


@router.post("/api/generate/{job_id}/markup/restore")
async def restore_generate_job_markup(
    job_id: str,
    source_sha256: str | None = Form(None),
    gallery_image_id: str | None = Form(None),
):
    path = await _load_job_layer_path(
        job_id,
        flag_key="markup_applied",
        filename=f"{job_id}-markup.png",
        metric_name="restore_generate_job_markup",
        missing_detail="No markup is stored for this job",
        file_missing_detail="Markup file not found",
    )
    await _verify_restore_layer_source(job_id, source_sha256, gallery_image_id)
    return _mask_response(path, None, verified=True, filename="markup.png")


@router.get("/api/generate/{job_id}/markup")
async def get_generate_job_markup(job_id: str):
    path = await _load_job_layer_path(
        job_id,
        flag_key="markup_applied",
        filename=f"{job_id}-markup.png",
        metric_name="get_generate_job_markup",
        missing_detail="No markup is stored for this job",
        file_missing_detail="Markup file not found",
    )
    # job_id is unique and the file is never rewritten after promotion, so a
    # retry re-fetching the same job's mask can cache it indefinitely.
    return _mask_response(path, None, verified=False, filename="markup.png")


@router.get("/api/generate/{job_id}/events")
async def stream_generate_job(job_id: str, request: Request):
    body = await generate_job_sse.stream_generate_job(
        job_id=job_id,
        client_ip=auth.get_client_ip(request),
        is_disconnected=request.is_disconnected,
    )
    return streaming_response(body)


@router.delete("/api/generate/{job_id}", response_model=MessageResponse)
async def cancel_generate_job(job_id: str):
    await cancel_image_job(job_id)
    return MessageResponse(status="success", message="Generation job cancelled")
