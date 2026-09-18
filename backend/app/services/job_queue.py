"""Image job request construction and durable queue admission."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


from ..runtime.state import MAX_GENERATE_JOBS, state
from .presets import (
    get_active_preset,
    get_api_presets,
    get_effective_preset_api_key,
    get_upstream_socks5_proxy,
    get_webhook_url,
    load_api_settings,
)
from ..core.errors import (
    InvalidRequestError,
    RateLimitedError,
    UnprocessableRequestError,
)
from ..core import settings as config
from ..core import validators as ssrf
from ..core.api_paths import normalize_default_model, normalize_default_response_format
from ..core.image_models import is_image_25
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.observability import metrics
from ..core.utils import utc_now
from ..repositories.image_jobs import (
    aggregate_image_job_units,
    count_pending_image_job_units,
    enqueue_image_job,
    get_pending_edit_source_bytes as get_persisted_pending_edit_source_bytes,
    release_edit_source_reservation,
    trim_generate_jobs as trim_persisted_generate_jobs,
)
from ..repositories.db import (
    EditSourceQueueFullError,
    ImageJobQueueFullError,
)
from ..repositories.db import image_url_for_filename
from ..schemas.gallery import GalleryEntry
from ..schemas.generation import EditRequest, GenerateJobResponse, GenerateRequest
from ..runtime.blocking import run_db_operation
from .job_events import (
    get_job_subscribers,
    get_jobs_subscribers,
    publish_generate_job,
    remember_generate_job_memory,
    store_generate_job,
    validate_job_webhook_url,
)


def kick_thumbnail_dispatcher() -> None:
    task = getattr(state, "thumbnail_dispatcher_task", None)
    event = getattr(state, "thumbnail_dispatcher_kick", None)
    if task and not task.done() and event is not None:
        event.set()


@dataclass(frozen=True)
class EditImageSource:
    temp_path: Path
    byte_size: int
    filename: str
    content_type: str
    role: Literal["image", "mask"] = "image"


def trim_generate_jobs():
    trim_persisted_generate_jobs(MAX_GENERATE_JOBS)


def get_generate_job_tasks() -> dict[str, asyncio.Task]:
    return state.generate_job_tasks


def get_pending_edit_source_bytes() -> int:
    return get_persisted_pending_edit_source_bytes()


def get_max_pending_edit_source_bytes() -> int:
    return max(0, config.MAX_PENDING_EDIT_SOURCE_MB) * 1024 * 1024


def release_pending_edit_source_bytes(job_id: str):
    if not job_id:
        return
    release_edit_source_reservation(job_id)


def get_image_unit_dispatcher_kick_event() -> asyncio.Event:
    event = getattr(state, "image_unit_dispatcher_kick", None)
    if event is None:
        event = asyncio.Event()
        state.image_unit_dispatcher_kick = event
    return event


def kick_image_unit_dispatcher():
    event = getattr(state, "image_unit_dispatcher_kick", None)
    if event is not None:
        event.set()


def request_image_units(req: GenerateRequest | EditRequest) -> int:
    try:
        return max(1, int(req.n or 1))
    except (TypeError, ValueError):
        return 1


def snapshot_queue_metrics() -> dict[str, int]:
    jobs = state.generate_jobs or {}
    repository_metrics = getattr(state, "image_queue_runtime_metrics", {})
    running_units = int(repository_metrics.get("running", 0))
    queued_units = int(repository_metrics.get("queued", 0))
    counts: dict[str, int] = {
        "image_jobs.active": 0,
        "image_jobs.active_units": running_units + queued_units,
        "image_jobs.queued": queued_units,
        "image_jobs.running": running_units,
        "image_jobs.capacity": config.MAX_ACTIVE_GENERATE_JOBS + config.MAX_QUEUED_GENERATE_JOBS,
        "image_jobs.running_capacity": config.MAX_ACTIVE_GENERATE_JOBS,
        "image_jobs.queued_capacity": config.MAX_QUEUED_GENERATE_JOBS,
        "image_jobs.upstream_request_capacity": config.MAX_ACTIVE_GENERATE_JOBS,
        "image_jobs.workers": config.GRANIAN_WORKERS,
        "image_jobs.per_worker_capacity": config.per_worker_generate_limit(),
        "image_jobs.tasks": len(get_generate_job_tasks()),
        "image_jobs.sse_job_subscribers": sum(
            len(subscribers)
            for subscribers in get_job_subscribers().values()
        ),
        "image_jobs.sse_jobs_subscribers": len(get_jobs_subscribers()),
        "edit_sources.pending_bytes": int(
            repository_metrics.get("pending_edit_source_bytes", 0)
        ),
        "edit_sources.pending_capacity_bytes": get_max_pending_edit_source_bytes(),
    }
    for operation in ("generation", "edit"):
        for status in ("queued", "running"):
            counts[f"image_jobs.{operation}.{status}.current"] = 0

    for job in jobs.values():
        status = str(job.get("status") or "")
        if status not in ACTIVE_GENERATE_JOB_STATUSES:
            continue
        operation = str(job.get("operation") or "generation")
        counts["image_jobs.active"] += 1
        if operation in {"generation", "edit"}:
            counts[f"image_jobs.{operation}.{status}.current"] += 1

    return counts


def build_pending_job(
    job_id: str,
    req: GenerateRequest | EditRequest,
    operation: str,
    message: str,
    api_path: str | None = None,
    api_preset_name: str | None = None,
    image_units: int = 1,
    mask_applied: bool = False,
) -> dict:
    now = utc_now()
    return {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "message": message,
        "operation": operation,
        "prompt": req.prompt,
        "size": req.size,
        "created_at": now,
        "updated_at": now,
        "model": req.model,
        "quality": req.quality,
        "output_format": req.output_format,
        "output_compression": req.output_compression,
        "background": req.background,
        "response_format": req.response_format,
        "n": req.n,
        "image_units": max(1, int(image_units or 1)),
        "api_path": api_path,
        "api_preset_name": api_preset_name,
        "streaming": bool(getattr(req, "stream", False)),
        "partial_images": getattr(req, "partial_images", None) if getattr(req, "stream", False) else None,
        "mask_applied": bool(mask_applied),
    }


def gallery_entry_job_image(entry: GalleryEntry) -> dict:
    return {
        "image_id": entry.id,
        "image_url": (
            image_url_for_filename(entry.filename)
            or f"/api/image/{entry.filename}"
        ),
        "filename": entry.filename,
        "image_width": entry.image_width,
        "image_height": entry.image_height,
    }


def gallery_entry_job_result(entry: GalleryEntry) -> dict:
    image = gallery_entry_job_image(entry)
    image["prompt"] = entry.prompt
    image["size"] = entry.size
    image["model"] = entry.model
    image["quality"] = entry.quality
    image["output_format"] = entry.output_format
    image["output_compression"] = entry.output_compression
    image["background"] = entry.background
    image["response_format"] = entry.response_format
    image["api_path"] = entry.api_path
    image["api_preset_name"] = entry.api_preset_name
    image["completed_at"] = entry.completed_at
    return image


def edit_source_to_payload(source: EditImageSource) -> dict:
    return {
        "temp_path": str(source.temp_path),
        "byte_size": source.byte_size,
        "filename": source.filename,
        "content_type": source.content_type,
        "role": source.role,
    }


def edit_source_from_payload(payload: dict) -> EditImageSource:
    role = str(payload.get("role") or "image")
    return EditImageSource(
        temp_path=Path(str(payload.get("temp_path") or "")),
        byte_size=int(payload.get("byte_size") or 0),
        filename=str(payload.get("filename") or "image.png"),
        content_type=str(payload.get("content_type") or "application/octet-stream"),
        role="mask" if role == "mask" else "image",
    )


def build_request_payload(req: GenerateRequest | EditRequest) -> dict:
    data = req.model_dump(mode="json")
    data.pop("webhook_url", None)
    return data


def rebuild_request(operation: str, payload: dict) -> GenerateRequest | EditRequest:
    request_data = {**payload, "n": 1}
    if operation == "edit":
        return EditRequest(**request_data)
    return GenerateRequest(**request_data)


def get_preset_for_unit(unit: dict) -> dict | None:
    load_api_settings()
    preset_id = str(unit.get("api_preset_id") or "")
    preset_name = str(unit.get("api_preset_name") or "")
    for preset in get_api_presets():
        if preset_id and preset.get("id") == preset_id:
            return preset
    for preset in get_api_presets():
        if preset_name and preset.get("name") == preset_name:
            return preset
    return get_active_preset()


def cleanup_parent_edit_sources(parent_job_id: str):
    aggregate = aggregate_image_job_units(parent_job_id)
    paths: set[str] = set()
    for unit in aggregate.get("units", []):
        for source in unit.get("edit_sources") or []:
            path = str(source.get("temp_path") or "")
            if path:
                paths.add(path)
    for path in paths:
        Path(path).unlink(missing_ok=True)
    release_pending_edit_source_bytes(parent_job_id)


def summarize_unit_failures(failures: list[dict], total: int, operation: str) -> str:
    if total == 1 and failures:
        return str(failures[0].get("error") or failures[0].get("message") or "failed")
    sample_messages = []
    for unit in failures[:3]:
        index = int(unit.get("unit_index") or 0)
        message = str(unit.get("error") or unit.get("message") or "failed")
        sample_messages.append(f"#{index + 1}: {message}")
    if len(failures) > len(sample_messages):
        sample_messages.append(f"... and {len(failures) - len(sample_messages)} more")
    suffix = "; ".join(sample_messages) if sample_messages else "no image data"
    noun = "image generation requests" if operation == "generation" else "image edit requests"
    return f"{len(failures)} of {total} {noun} failed: {suffix}"


def build_edit_request_from_form(
    prompt: str,
    size: str,
    model: str,
    n: int,
    quality: str,
    output_format: str,
    output_compression: int | None,
    response_format: str | None,
    webhook_url: str | None,
    background: str = "auto",
) -> EditRequest:
    try:
        return EditRequest(
            prompt=prompt,
            size=size,
            model=model,
            n=n,
            quality=quality,
            output_format=output_format,
            output_compression=output_compression,
            background=background,
            response_format=response_format,
            webhook_url=webhook_url,
        )
    except ValueError as e:
        raise UnprocessableRequestError(str(e)) from e


async def queue_image_job(
    *,
    req: GenerateRequest | EditRequest,
    operation: Literal["generation", "edit"],
    api_path: str | Callable[[dict], str],
    queued_message: str,
    pending_edit_source_bytes: int = 0,
    edit_sources_payload: list[dict] | None = None,
    mask_applied: bool = False,
) -> GenerateJobResponse:
    await run_db_operation(load_api_settings, metric_name="load_api_settings")
    active_preset = get_active_preset()
    active_preset_id = str(active_preset.get("id") or "default")
    api_url = str(active_preset.get("api_url") or "").rstrip("/")
    api_preset_name = active_preset.get("name") or "Untitled preset"
    resolved_api_path = api_path(active_preset) if callable(api_path) else api_path
    requested_model = (
        str(req.model or "").strip()
        if "model" in getattr(req, "model_fields_set", set())
        else ""
    )
    req.model = requested_model or normalize_default_model(
        active_preset.get("default_model"),
        resolved_api_path,
    )
    if "response_format" not in getattr(req, "model_fields_set", set()):
        default_response_format = normalize_default_response_format(
            active_preset.get("default_response_format")
        )
        req.response_format = default_response_format or None

    try:
        req.normalize_model_options(resolved_api_path)
    except ValueError as e:
        raise UnprocessableRequestError(str(e)) from e
    if operation == "edit" and is_image_25(req.model):
        for source in edit_sources_payload or []:
            if source.get("content_type") not in {"image/png", "image/jpeg", "image/webp"}:
                raise UnprocessableRequestError("GPT Image 2.5 edit inputs must be PNG, JPEG or WebP; convert this image before editing")
            if int(source.get("byte_size") or 0) >= 50 * 1024 * 1024:
                raise UnprocessableRequestError("GPT Image 2.5 edit inputs must be smaller than 50 MB")

    if getattr(req, "stream", False) and resolved_api_path not in {
        "/v1/images/generations",
        "/v1/images/edits",
    }:
        raise UnprocessableRequestError("Streaming preview requires /v1/images/generations or /v1/images/edits")

    if not api_url:
        raise InvalidRequestError("API URL not configured. Please set it in Settings.")
    try:
        ssrf.normalize_upstream_base_url(api_url)
    except ValueError as e:
        raise InvalidRequestError(str(e)) from e
    get_effective_preset_api_key(active_preset)
    get_upstream_socks5_proxy()

    webhook_url = await asyncio.to_thread(
        validate_job_webhook_url,
        req.webhook_url or get_webhook_url(),
    )
    image_units = request_image_units(req)
    job_id = str(uuid.uuid4())
    pending_job = build_pending_job(
        job_id=job_id,
        req=req,
        operation=operation,
        message=queued_message,
        api_path=resolved_api_path,
        api_preset_name=api_preset_name,
        image_units=image_units,
        mask_applied=mask_applied,
    )
    pending_job["webhook_url"] = webhook_url
    try:
        stored_job, _units = await run_db_operation(
            enqueue_image_job,
            parent_job=pending_job,
            operation=operation,
            request=build_request_payload(req),
            image_units=image_units,
            api_preset_id=active_preset_id,
            api_preset_name=api_preset_name,
            api_path=resolved_api_path,
            edit_sources=edit_sources_payload,
            pending_edit_source_bytes=pending_edit_source_bytes,
            max_active_generate_jobs=config.MAX_ACTIVE_GENERATE_JOBS,
            max_queued_generate_jobs=config.MAX_QUEUED_GENERATE_JOBS,
            max_pending_edit_source_bytes=get_max_pending_edit_source_bytes(),
            metric_name="enqueue_image_job",
        )
    except ImageJobQueueFullError as e:
        metrics.increment("image_jobs.rejected.queue_full")
        raise RateLimitedError("Generation job queue is full") from e
    except EditSourceQueueFullError as e:
        metrics.increment("image_jobs.rejected.edit_source_full")
        raise RateLimitedError("Edit source queue is full") from e
    except Exception:
        raise

    remember_generate_job_memory(job_id, stored_job)
    state.generate_job_last_persist_at.pop(job_id, None)
    metrics.increment(f"image_jobs.{operation}.queued")
    publish_generate_job(stored_job, list_debounce=False, list_reconcile=True)
    kick_image_unit_dispatcher()

    return GenerateJobResponse(
        job_id=job_id,
        status="queued",
        stage="queued",
        message=queued_message,
        operation=operation,
    )


async def queue_edit_job(
    req: EditRequest,
    image_sources: list[EditImageSource],
    mask_source: EditImageSource | None = None,
) -> GenerateJobResponse:
    edit_sources = [*image_sources, mask_source] if mask_source is not None else image_sources
    edit_source_bytes = sum(source.byte_size for source in edit_sources)

    return await queue_image_job(
        req=req,
        operation="edit",
        api_path="/v1/images/edits",
        queued_message="Queued image edit",
        pending_edit_source_bytes=edit_source_bytes,
        edit_sources_payload=[edit_source_to_payload(source) for source in edit_sources],
        mask_applied=mask_source is not None,
    )
