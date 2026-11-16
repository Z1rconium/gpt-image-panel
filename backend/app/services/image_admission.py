"""Single owner of image generation and edit job admission rules."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from ..core import settings as config
from ..core.api_paths import (
    PROVIDER_KIND_ASYNC_JSON,
    normalize_default_model,
    normalize_default_response_format,
    normalize_provider_kind,
)
from ..core.errors import UnprocessableRequestError
from ..core.image_models import is_image_25, is_image_26
from ..core.media import is_below_edit_input_limit
from ..core.provider_mapping import template_variables
from ..repositories.db.execution import run_db_operation
from ..runtime.blocking import run_image_operation
from ..schemas.generation import EditRequest, GenerateRequest
from ..schemas.provider import provider_capabilities, resolve_provider_config
from .edit_markup import validate_edit_markup
from .edit_masks import (
    EditMaskInfo,
    validate_edit_mask_against_primary,
    validate_edit_mask_against_primary_path,
)
from .edit_source_types import EditImageSource
from .image_preset_snapshot import apply_image_preset_snapshot
from .presets import get_active_preset, get_api_presets, load_api_settings

_STREAMABLE_API_PATHS = frozenset(
    {
        "/v1/images/generations",
        "/v1/images/edits",
        "/v1/responses",
    }
)
_IMAGE_25_26_EDIT_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
    }
)


@dataclass(frozen=True)
class EditInputs:
    """Staged edit inputs ready for admission, primary source first."""

    sources: tuple[EditImageSource, ...]
    mask: EditImageSource | None = None
    markup: EditImageSource | None = None

    @property
    def all_sources(self) -> tuple[EditImageSource, ...]:
        extra = (
            *((self.mask,) if self.mask is not None else ()),
            *((self.markup,) if self.markup is not None else ()),
        )
        return (*self.sources, *extra)


@dataclass(frozen=True)
class AdmittedJob:
    """Immutable admission outcome carrying a normalized copy of the request."""

    request: GenerateRequest | EditRequest
    operation: Literal["generation", "edit"]
    preset: Mapping[str, Any]
    api_path: str
    model: str
    mask_info: EditMaskInfo | None = None
    edit_inputs: EditInputs | None = None


def resolve_effective_preset(
    req: GenerateRequest | EditRequest | str | None,
    active: Mapping[str, Any] | None,
    presets: Iterable[Mapping[str, Any]],
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the effective preset for a request, applying pin and turn snapshot."""
    api_preset_id = req.api_preset_id if isinstance(req, GenerateRequest) else req
    effective = dict(active) if active is not None else {}
    if api_preset_id:
        # Retrying a historical job may pin the preset it ran with, without
        # switching the globally active preset.
        matched = next(
            (preset for preset in presets if preset.get("id") == api_preset_id),
            None,
        )
        if matched is None:
            raise UnprocessableRequestError("API preset not found")
        effective = dict(matched)
    if snapshot is not None:
        effective = apply_image_preset_snapshot(effective, dict(snapshot))
    return effective


def _resolved_model_name(
    req: GenerateRequest | EditRequest,
    preset: Mapping[str, Any],
    api_path: str,
) -> str:
    fields_set = getattr(req, "model_fields_set", set())
    requested_model = (
        str(req.model or "").strip()
        if "model" in fields_set
        else ""
    )
    return requested_model or normalize_default_model(
        preset.get("default_model"),
        api_path,
    )


def _validate_async_provider_capabilities(
    req: GenerateRequest | EditRequest,
    *,
    operation: Literal["generation", "edit"],
    preset: Mapping[str, Any],
    edit: EditInputs | None,
) -> None:
    if normalize_provider_kind(preset.get("provider_kind")) != PROVIDER_KIND_ASYNC_JSON:
        return

    # Admission and the frontend both read this single capability view of
    # the mapping, so a task never enters the queue on an unstated ability.
    provider_caps = provider_capabilities(preset.get("provider_config"))
    resolved_provider = resolve_provider_config(preset.get("provider_config"))
    active_submit = (
        resolved_provider.edit_submit if operation == "edit" else resolved_provider.submit
    )
    active_variables = (
        template_variables(active_submit.body) if active_submit is not None else set()
    )
    if req.complexity is not None and "complexity" not in active_variables:
        raise UnprocessableRequestError(
            "Provider mapping does not send complexity for this operation"
        )
    if req.output_format == "gif" and not {"frame_count", "frame_duration_ms"} <= active_variables:
        raise UnprocessableRequestError(
            "Provider mapping does not send animation controls for this operation"
        )
    if req.complexity is not None and not provider_caps.complexity:
        raise UnprocessableRequestError("Provider mapping does not send complexity")
    if req.output_format == "gif" and not provider_caps.animation:
        raise UnprocessableRequestError(
            "Provider mapping does not send animation controls"
        )
    all_sources = edit.all_sources if edit is not None else ()
    if any(source.role == "markup" for source in all_sources) and not provider_caps.markup:
        raise UnprocessableRequestError("Provider mapping does not send markup")
    if operation == "edit":
        if not provider_caps.edit:
            raise UnprocessableRequestError(
                "This provider mapping does not declare image edit support; "
                "add an edit_submit section to provider_config"
            )
        if any(source.role == "mask" for source in all_sources) and not provider_caps.mask:
            raise UnprocessableRequestError(
                "This provider mapping cannot send an edit mask; configure "
                "edit_submit.files.mask (multipart) or {{mask}} in a JSON body"
            )
    if getattr(req, "stream", False):
        raise UnprocessableRequestError(
            "Streaming preview is not available for async providers"
        )
    if req.background == "transparent" and not provider_caps.transparent_background:
        raise UnprocessableRequestError(
            "This provider mapping does not declare native transparent background "
            "support; pick a chroma background or declare "
            "capabilities.transparent_background in provider_config"
        )
    if req.output_format not in provider_caps.formats:
        raise UnprocessableRequestError(
            "This provider mapping supports output formats "
            f"{'/'.join(provider_caps.formats)}; '{req.output_format}' was requested"
        )


def admit_image_job(
    req: GenerateRequest | EditRequest,
    *,
    operation: Literal["generation", "edit"],
    preset: Mapping[str, Any],
    api_path: str,
    edit: EditInputs | None = None,
    mask_info: EditMaskInfo | None = None,
    mask_paste_back_default: bool = True,
) -> AdmittedJob:
    """Validate and normalize an image job request without mutating ``req``."""
    fields_set = set(getattr(req, "model_fields_set", set()))
    effective_model = _resolved_model_name(req, preset, api_path)

    normalized = req.model_copy()
    normalized.model = effective_model
    if "response_format" not in fields_set:
        default_response_format = normalize_default_response_format(
            preset.get("default_response_format")
        )
        normalized.response_format = default_response_format or None

    if operation == "edit" and edit is not None:
        if edit.mask is not None and edit.markup is not None:
            raise UnprocessableRequestError("Mask and markup cannot be combined")
        if edit.markup is not None and not is_image_26(effective_model):
            raise UnprocessableRequestError("Markup requires GPT Image 2.6")

    try:
        normalized.normalize_model_options(api_path)
    except ValueError as e:
        raise UnprocessableRequestError(str(e)) from e
    if normalized.output_format in {"svg", "gif"} and "partial_images" in fields_set:
        raise UnprocessableRequestError("SVG/GIF does not support partial_images")

    resolved_edit = edit
    if operation == "edit" and edit is not None:
        if edit.mask is not None and normalized.output_format == "gif":
            raise UnprocessableRequestError("GIF output does not support edit masks")
        if isinstance(normalized, EditRequest):
            if edit.markup is not None:
                normalized.paste_back = False
            elif edit.mask is not None and normalized.paste_back is None:
                normalized.paste_back = mask_paste_back_default
        if edit.mask is not None and mask_info is not None:
            resolved_edit = replace(
                edit,
                mask=replace(edit.mask, coverage=mask_info.transparent_ratio),
            )

    if operation == "edit" and (is_image_25(normalized.model) or is_image_26(normalized.model)):
        for source in resolved_edit.all_sources if resolved_edit is not None else ():
            if source.content_type not in _IMAGE_25_26_EDIT_CONTENT_TYPES:
                raise UnprocessableRequestError(
                    "GPT Image 2.5/2.6 edit inputs must be PNG, JPEG or WebP; convert this image before editing"
                )
            if not is_below_edit_input_limit(source.byte_size):
                raise UnprocessableRequestError(
                    "GPT Image 2.5/2.6 edit inputs must be smaller than 50 MB"
                )

    _validate_async_provider_capabilities(
        normalized,
        operation=operation,
        preset=preset,
        edit=resolved_edit,
    )

    if getattr(normalized, "stream", False) and api_path not in _STREAMABLE_API_PATHS:
        raise UnprocessableRequestError(
            "Streaming preview requires /v1/images/generations, /v1/images/edits or /v1/responses"
        )

    return AdmittedJob(
        request=normalized,
        operation=operation,
        preset=dict(preset),
        api_path=api_path,
        model=normalized.model,
        mask_info=mask_info,
        edit_inputs=resolved_edit,
    )


async def validate_uploaded_markup(
    markup: EditImageSource | None,
    primary: EditImageSource,
) -> None:
    """Validate a staged markup PNG file against the primary image dimensions."""
    if markup is None:
        return
    try:
        await run_image_operation(
            validate_edit_markup,
            markup.temp_path,
            primary.width,
            primary.height,
            metric_name="validate_edit_markup",
        )
    except (ValueError, OSError) as exc:
        raise UnprocessableRequestError(str(exc)) from exc


async def _validate_uploaded_mask(
    mask: EditImageSource,
    primary: EditImageSource,
) -> EditMaskInfo:
    try:
        if primary.width > 0 and primary.height > 0:
            return await run_image_operation(
                validate_edit_mask_against_primary,
                mask.temp_path,
                primary_width=primary.width,
                primary_height=primary.height,
                metric_name="validate_edit_mask",
            )
        # Defensive fallback for a primary admitted without a cached size
        # (e.g. a legacy in-flight request); decodes the primary once more.
        return await run_image_operation(
            validate_edit_mask_against_primary_path,
            mask.temp_path,
            primary.temp_path,
            primary_filename=primary.filename,
            primary_content_type=primary.content_type,
            metric_name="validate_edit_mask",
        )
    except ValueError as e:
        raise UnprocessableRequestError(str(e)) from e


async def admit_generation(
    req: GenerateRequest,
    *,
    api_path_for: Callable[[dict[str, Any]], str],
    preset_snapshot: dict[str, Any] | None = None,
) -> AdmittedJob:
    """Load settings, resolve the effective preset, and admit a generation job."""
    await run_db_operation(load_api_settings, metric_name="load_api_settings")
    preset = resolve_effective_preset(
        req,
        get_active_preset(),
        get_api_presets(),
        preset_snapshot,
    )
    return admit_image_job(
        req,
        operation="generation",
        preset=preset,
        api_path=api_path_for(preset),
    )


async def admit_edit(
    req: EditRequest,
    edit: EditInputs,
    *,
    preset_snapshot: dict[str, Any] | None = None,
) -> AdmittedJob:
    """Load settings, validate markup/mask files in order, and admit an edit job."""
    await run_db_operation(load_api_settings, metric_name="load_api_settings")
    preset = resolve_effective_preset(
        req,
        get_active_preset(),
        get_api_presets(),
        preset_snapshot,
    )
    if edit.mask is not None and edit.markup is not None:
        raise UnprocessableRequestError("Mask and markup cannot be combined")
    if edit.markup is not None:
        if not edit.sources:
            raise UnprocessableRequestError("Upload image is required.")
        if not is_image_26(_resolved_model_name(req, preset, "/v1/images/edits")):
            raise UnprocessableRequestError("Markup requires GPT Image 2.6")
        await validate_uploaded_markup(edit.markup, edit.sources[0])
    mask_info: EditMaskInfo | None = None
    if edit.mask is not None:
        if not edit.sources:
            raise UnprocessableRequestError("Upload image is required.")
        mask_info = await _validate_uploaded_mask(edit.mask, edit.sources[0])
    return admit_image_job(
        req,
        operation="edit",
        preset=preset,
        api_path="/v1/images/edits",
        edit=edit,
        mask_info=mask_info,
        mask_paste_back_default=config.MASK_PASTE_BACK_DEFAULT,
    )
