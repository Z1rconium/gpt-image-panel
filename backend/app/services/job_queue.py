"""Image job request construction and durable queue admission."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..runtime.state import MAX_GENERATE_JOBS, state
from . import edit_source_types
from .image_admission import (
    AdmittedJob,
    EditInputs,
    admit_edit,
    admit_generation,
    admit_image_job,
    resolve_effective_preset,
)
from .preset_credentials import get_effective_preset_api_key
from .presets import (
    get_active_preset,
    get_api_presets,
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
from ..core.api_paths import normalize_default_model
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.observability import metrics
from ..core.utils import utc_now
from ..repositories.image_files import copy_mask_file, delete_mask_file, write_mask_file
from ..repositories.image_jobs import (
    aggregate_image_job_units,
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
from ..repositories.db.execution import run_db_operation
from .job_events import (
    get_job_subscribers,
    get_jobs_subscribers,
    publish_generate_job,
    remember_generate_job_memory,
)
from .webhook_service import validate_job_webhook_url


def kick_thumbnail_dispatcher() -> None:
    task = getattr(state, "thumbnail_dispatcher_task", None)
    event = getattr(state, "thumbnail_dispatcher_kick", None)
    if task and not task.done() and event is not None:
        event.set()


def resolve_effective_model(
    model: str | None,
    *,
    api_preset_id: str | None = None,
    api_path: str = "/v1/images/edits",
    get_active: Callable[[], dict] = get_active_preset,
    get_presets: Callable[[], list[dict]] = get_api_presets,
) -> str:
    """Resolve explicit or preset-default image model without mutating request objects."""
    try:
        active_preset = resolve_effective_preset(
            api_preset_id,
            get_active(),
            get_presets(),
            None,
        )
    except UnprocessableRequestError:
        active_preset = {}
    explicit = str(model or "").strip()
    return explicit or normalize_default_model(
        active_preset.get("default_model"),
        api_path,
    )


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
    admitted: AdmittedJob,
    message: str,
    image_units: int = 1,
) -> dict:
    req = admitted.request
    preset = admitted.preset
    mask_applied = bool(admitted.edit_inputs and admitted.edit_inputs.mask is not None)
    now = utc_now()
    return {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "message": message,
        "operation": admitted.operation,
        "prompt": req.prompt,
        "size": req.size,
        "created_at": now,
        "updated_at": now,
        "model": req.model,
        "quality": req.quality,
        "output_format": req.output_format,
        "output_compression": req.output_compression,
        "complexity": req.complexity,
        "frame_count": req.frame_count,
        "frame_duration_ms": req.frame_duration_ms,
        "background": req.background,
        "response_format": req.response_format,
        "n": req.n,
        "image_units": max(1, int(image_units or 1)),
        "api_path": admitted.api_path,
        "api_preset_id": str(preset.get("id") or "default"),
        "api_preset_name": preset.get("name") or "Untitled preset",
        "streaming": bool(getattr(req, "stream", False)),
        "partial_images": getattr(req, "partial_images", None) if getattr(req, "stream", False) else None,
        "mask_applied": mask_applied,
        "paste_back": req.paste_back if isinstance(req, EditRequest) and mask_applied else None,
        "safety_identifier": getattr(req, "safety_identifier", None),
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
        "sent_prompt": entry.sent_prompt,
        "revised_prompt": entry.revised_prompt,
        "reported_size": entry.reported_size,
        "reported_quality": entry.reported_quality,
        "upstream_duration_ms": entry.upstream_duration_ms,
        "chroma_status": entry.chroma_status,
        "diagnostics": list(entry.diagnostics),
        "paste_back": entry.paste_back,
        "paste_back_scale": entry.paste_back_scale,
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


def build_request_payload(req: GenerateRequest | EditRequest) -> dict:
    data = req.model_dump(mode="json")
    data.pop("webhook_url", None)
    return data


def rebuild_request(operation: str, payload: dict) -> GenerateRequest | EditRequest:
    request_data = {**payload, "n": 1}
    request_data.pop("_provider_snapshot", None)
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
    paste_back: bool | None = None,
    api_preset_id: str | None = None,
    complexity: str | None = None,
    frame_count: int | None = None,
    frame_duration_ms: int | None = None,
    safety_identifier: str | None = None,
) -> EditRequest:
    try:
        return EditRequest(
            prompt=prompt,
            size=size,
            model=model,
            n=n,
            quality=quality,
            complexity=complexity,
            output_format=output_format,
            frame_count=frame_count,
            frame_duration_ms=frame_duration_ms,
            output_compression=output_compression,
            background=background,
            response_format=response_format,
            webhook_url=webhook_url,
            paste_back=paste_back,
            api_preset_id=api_preset_id or None,
            safety_identifier=safety_identifier,
        )
    except ValueError as e:
        raise UnprocessableRequestError(str(e)) from e


async def _cleanup_retry_layer_copies(
    job_id: str,
    *,
    wrote_mask: bool,
    wrote_markup: bool,
) -> None:
    if wrote_markup:
        await asyncio.to_thread(delete_mask_file, f"{job_id}-markup.png")
    if wrote_mask:
        await asyncio.to_thread(delete_mask_file, f"{job_id}.png")


async def enqueue(
    admitted: AdmittedJob,
    *,
    queued_message: str | None = None,
    job_id: str | None = None,
    agent_turn_id: str | None = None,
    agent_conversation_id: str | None = None,
) -> GenerateJobResponse:
    """Run impure preflight, write retry copies, and persist an admitted job."""
    req = admitted.request
    preset = admitted.preset
    active_preset_id = str(preset.get("id") or "default")
    api_url = str(preset.get("api_url") or "").rstrip("/")
    api_preset_name = preset.get("name") or "Untitled preset"
    message = queued_message or (
        "Queued image generation"
        if admitted.operation == "generation"
        else "Queued image edit"
    )

    if not api_url:
        raise InvalidRequestError("API URL not configured. Please set it in Settings.")
    try:
        ssrf.normalize_upstream_base_url(api_url)
    except ValueError as e:
        raise InvalidRequestError(str(e)) from e
    get_effective_preset_api_key(preset)
    get_upstream_socks5_proxy()

    webhook_url = await asyncio.to_thread(
        validate_job_webhook_url,
        req.webhook_url or get_webhook_url(),
    )

    edit_inputs = admitted.edit_inputs
    edit_sources = edit_inputs.all_sources if edit_inputs is not None else ()
    edit_sources_payload = (
        [edit_source_types.edit_source_to_payload(source) for source in edit_sources]
        if edit_sources
        else None
    )
    pending_edit_source_bytes = sum(source.byte_size for source in edit_sources)
    has_mask = edit_inputs is not None and edit_inputs.mask is not None
    has_markup = edit_inputs is not None and edit_inputs.markup is not None

    image_units = request_image_units(req)
    job_id = job_id or str(uuid.uuid4())
    pending_job = build_pending_job(
        job_id=job_id,
        admitted=admitted,
        message=message,
        image_units=image_units,
    )
    pending_job["webhook_url"] = webhook_url
    pending_job["agent_turn_id"] = agent_turn_id
    pending_job["agent_conversation_id"] = agent_conversation_id
    pending_job["markup_applied"] = has_markup
    request_payload = build_request_payload(req)
    request_payload["_provider_snapshot"] = {
        key: preset.get(key)
        for key in ("api_url", "provider_kind", "provider_config", "prompt_guard", "supports_mask")
    }

    wrote_mask = False
    wrote_markup = False
    try:
        if has_mask:
            if admitted.mask_info is None:
                raise ValueError("mask_info is required when edit_inputs.mask is set")
            # The retry copy in MASKS_DIR is a re-encoded `LA` PNG built once,
            # during the mask's single admission-time decode (see
            # services.edit_masks.validate_edit_mask_file); the raw upload at
            # edit_inputs.mask.temp_path is untouched and sent upstream.
            await asyncio.to_thread(
                write_mask_file,
                admitted.mask_info.optimized_png,
                f"{job_id}.png",
            )
            wrote_mask = True
        if has_markup:
            assert edit_inputs is not None and edit_inputs.markup is not None
            await asyncio.to_thread(
                copy_mask_file,
                edit_inputs.markup.temp_path,
                f"{job_id}-markup.png",
            )
            wrote_markup = True

        stored_job, _units = await run_db_operation(
            enqueue_image_job,
            parent_job=pending_job,
            operation=admitted.operation,
            request=request_payload,
            image_units=image_units,
            api_preset_id=active_preset_id,
            api_preset_name=api_preset_name,
            api_path=admitted.api_path,
            edit_sources=edit_sources_payload,
            pending_edit_source_bytes=pending_edit_source_bytes,
            max_active_generate_jobs=config.MAX_ACTIVE_GENERATE_JOBS,
            max_queued_generate_jobs=config.MAX_QUEUED_GENERATE_JOBS,
            max_pending_edit_source_bytes=get_max_pending_edit_source_bytes(),
            metric_name="enqueue_image_job",
        )
    except ImageJobQueueFullError as e:
        await _cleanup_retry_layer_copies(
            job_id, wrote_mask=wrote_mask, wrote_markup=wrote_markup
        )
        metrics.increment("image_jobs.rejected.queue_full")
        raise RateLimitedError("Generation job queue is full") from e
    except EditSourceQueueFullError as e:
        await _cleanup_retry_layer_copies(
            job_id, wrote_mask=wrote_mask, wrote_markup=wrote_markup
        )
        metrics.increment("image_jobs.rejected.edit_source_full")
        raise RateLimitedError("Edit source queue is full") from e
    except BaseException:
        await _cleanup_retry_layer_copies(
            job_id, wrote_mask=wrote_mask, wrote_markup=wrote_markup
        )
        raise

    remember_generate_job_memory(job_id, stored_job)
    state.generate_job_last_persist_at.pop(job_id, None)
    metrics.increment(f"image_jobs.{admitted.operation}.queued")
    publish_generate_job(stored_job, list_debounce=False, list_reconcile=True)
    kick_image_unit_dispatcher()

    return GenerateJobResponse(
        job_id=job_id,
        status="queued",
        stage="queued",
        message=message,
        operation=admitted.operation,
    )


@dataclass(frozen=True)
class AgentOrigin:
    """Agent turn that owns a queued job, plus the preset snapshot pinned to it."""

    turn_id: str
    conversation_id: str
    preset_snapshot: dict | None = None


async def queue_image_job(
    *,
    req: GenerateRequest | EditRequest,
    operation: Literal["generation", "edit"],
    api_path: str | Callable[[dict], str],
    queued_message: str,
    pending_edit_source_bytes: int = 0,
    edit_sources_payload: list[dict] | None = None,
    mask_applied: bool = False,
    job_id: str | None = None,
    agent: AgentOrigin | None = None,
) -> GenerateJobResponse:
    del pending_edit_source_bytes, mask_applied
    preset_snapshot = agent.preset_snapshot if agent else None
    if operation == "generation":
        admitted = await admit_generation(
            req,
            api_path_for=api_path if callable(api_path) else (lambda _preset: api_path),
            preset_snapshot=preset_snapshot,
        )
    else:
        await run_db_operation(load_api_settings, metric_name="load_api_settings")
        preset = resolve_effective_preset(
            req,
            get_active_preset(),
            get_api_presets(),
            preset_snapshot,
        )
        resolved_api_path = api_path(preset) if callable(api_path) else api_path
        sources = [
            edit_source_types.edit_source_from_payload(p)
            for p in (edit_sources_payload or [])
        ]
        edit = (
            EditInputs(
                sources=tuple(s for s in sources if s.role not in {"mask", "markup"}),
                mask=next((s for s in sources if s.role == "mask"), None),
                markup=next((s for s in sources if s.role == "markup"), None),
            )
            if sources
            else None
        )
        admitted = admit_image_job(
            req,
            operation="edit",
            preset=preset,
            api_path=resolved_api_path,
            edit=edit,
            mask_paste_back_default=config.MASK_PASTE_BACK_DEFAULT,
        )
    return await enqueue(
        admitted,
        queued_message=queued_message,
        job_id=job_id,
        agent_turn_id=agent.turn_id if agent else None,
        agent_conversation_id=agent.conversation_id if agent else None,
    )


async def queue_edit_job(
    req: EditRequest,
    image_sources: list[edit_source_types.EditImageSource],
    mask_source: edit_source_types.EditImageSource | None = None,
    mask_coverage: float | None = None,
    mask_optimized_png: bytes | None = None,
    markup_source: edit_source_types.EditImageSource | None = None,
    *,
    agent: AgentOrigin | None = None,
) -> GenerateJobResponse:
    del mask_coverage, mask_optimized_png
    admitted = await admit_edit(
        req,
        EditInputs(
            sources=tuple(image_sources),
            mask=mask_source,
            markup=markup_source,
        ),
        preset_snapshot=agent.preset_snapshot if agent else None,
    )
    return await enqueue(
        admitted,
        queued_message="Queued image edit",
        agent_turn_id=agent.turn_id if agent else None,
        agent_conversation_id=agent.conversation_id if agent else None,
    )
