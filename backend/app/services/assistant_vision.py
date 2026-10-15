"""Read-only gallery vision analysis (describe / prompt / analyze).

Prompt optimization that renders and compares a preview image lives in
:mod:`assistant_prompt_optimization`.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from ..repositories.db.execution import run_db_operation
from ..core.image_models import MAX_PROMPT_CHARS
from . import vision_previews
from ..core.errors import (
    DomainError,
    InvalidRequestError,
    NotFoundError,
    UpstreamError,
    UpstreamTimeoutError,
)
from ..core.utils import json_dumps_canonical
from ..integrations import assistant_client
from ..repositories.gallery.mutations import is_gallery_filename_referenced
from ..repositories.gallery.queries import get_gallery_entry
from ..core.media import safe_image_path
from ..repositories.settings import (
    get_gallery_ai_metadata,
    upsert_gallery_ai_metadata,
)
from ..schemas.assistant import (
    AssistantGalleryImageResponse,
    AssistantGalleryMetadataResponse,
)
from .assistant_runtime import (
    AssistantRuntime,
    _assistant_request_limit,
    _batch_assistant_runtime,
    _batch_target_language,
    _clamp_text,
    _image_prompt_system_prompt,
    _resolve_runtime_async,
    _string_list,
    _truncate_assistant_data,
    _warnings,
)

logger = logging.getLogger(__name__)


async def _gallery_entry_and_preview(image_id: str) -> tuple[Any, dict[str, Any]]:
    entry = await run_db_operation(get_gallery_entry, image_id)
    if not entry:
        raise NotFoundError("Gallery entry not found")
    path = await asyncio.to_thread(safe_image_path, entry.filename)
    if not path or not Path(path).is_file():
        raise NotFoundError("Gallery image file not found")
    if not await asyncio.to_thread(is_gallery_filename_referenced, entry.filename):
        raise NotFoundError("Gallery image file is not referenced")
    preview = await vision_previews.prepare_preview(Path(path))
    return entry, preview  # type: ignore[return-value]


async def _assistant_vision_json(
    image_id: str,
    *,
    system_prompt: str,
    schema: dict[str, Any],
    include_stored_prompt: bool = True,
    wait_for_slot: bool = False,
    max_tokens: int = 900,
    temperature: float = 0.2,
    runtime: AssistantRuntime | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any], str, int]:
    if runtime is None:
        runtime_holder = _batch_assistant_runtime.get()
        if runtime_holder is not None:
            runtime_holder.runtime = runtime_holder.runtime or await _resolve_runtime_async(vision=True)
            runtime = runtime_holder.runtime
        else:
            runtime = await _resolve_runtime_async(vision=True)
    async with _assistant_request_limit(runtime.timeout_seconds, wait_for_slot=wait_for_slot):
        try:
            entry, preview = await _gallery_entry_and_preview(image_id)
            data, model_used, duration_ms = await assistant_client.request_assistant_json(
                api_url=runtime.api_url,
                api_key=runtime.api_key,
                api_path=runtime.api_path,
                model=runtime.model,
                system_prompt=system_prompt,
                user_prompt=_gallery_vision_user_prompt(
                    entry,
                    preview,
                    include_stored_prompt=include_stored_prompt,
                ),
                schema=schema,
                image=preview,
                timeout_seconds=runtime.timeout_seconds,
                max_tokens=max_tokens,
                temperature=temperature,
                prevalidated_endpoint=runtime.endpoint,
            )
        except assistant_client.AssistantTimeoutError as e:
            raise UpstreamTimeoutError(str(e)) from e
        except assistant_client.AssistantError as e:
            status_code = 400 if e.status == 400 else 502
            raise DomainError(str(e), status_code=status_code) from e
        except ValueError as e:
            raise InvalidRequestError(str(e)) from e
        return entry, preview, _truncate_assistant_data(data), model_used, duration_ms


def _gallery_vision_user_prompt(entry: Any, preview: dict[str, Any], *, include_stored_prompt: bool = True) -> str:
    payload: dict[str, Any] = {
        "image_id": entry.id,
        "size": entry.size,
        "model": entry.model,
        "api_path": entry.api_path,
        "preview": {key: preview[key] for key in ("bytes", "width", "height", "source_has_alpha")},
    }
    if include_stored_prompt:
        payload["stored_prompt"] = entry.prompt
    return json_dumps_canonical(payload)


def _normalize_gallery_analysis(value: Any) -> dict[str, object]:
    analysis = _truncate_assistant_data(value if isinstance(value, dict) else {})
    if not isinstance(analysis, dict):
        return {}
    return {
        "subjects": _string_list(analysis.get("subjects"), max_items=12),
        "style": _clamp_text(analysis.get("style"), 500),
        "composition": _clamp_text(analysis.get("composition"), 800),
        "lighting": _clamp_text(analysis.get("lighting"), 500),
        "colors": _string_list(analysis.get("colors"), max_items=12),
    }


def _prepare_gallery_ai_metadata(
    *,
    description: Any = "",
    prompt: Any = "",
    analysis: Any = None,
) -> tuple[str, str, dict[str, object]]:
    return (
        _clamp_text(description, 2000),
        _clamp_text(prompt, MAX_PROMPT_CHARS),
        _normalize_gallery_analysis(analysis),
    )


async def _analyze_gallery_image(
    image_id: str,
    *,
    persist: bool,
    wait_for_slot: bool = False,
    target_language: Literal["en", "zh-CN"] | None = None,
    runtime: AssistantRuntime | None = None,
) -> AssistantGalleryImageResponse:
    target_language = target_language or _batch_target_language.get()
    _entry, _preview, data, model, duration_ms = await _assistant_vision_json(
        image_id,
        system_prompt=(
            "You analyze a local gallery image. Return a concise description, a useful reverse-engineered "
            "generation prompt, and structured visual metadata. "
            + _image_prompt_system_prompt(target_language)
        ),
        schema={
            "description": "string",
            "prompt": "string",
            "analysis": {
                "subjects": ["string"],
                "style": "string",
                "composition": "string",
                "lighting": "string",
                "colors": ["string"],
            },
            "warnings": ["string"],
        },
        wait_for_slot=wait_for_slot,
        max_tokens=900,
        temperature=0.2,
        runtime=runtime,
    )
    description, prompt, analysis = _prepare_gallery_ai_metadata(
        description=data.get("description"),
        prompt=data.get("prompt"),
        analysis=data.get("analysis"),
    )
    if not prompt:
        raise UpstreamError("AI Assistant returned an empty image prompt")
    if persist:
        await run_db_operation(upsert_gallery_ai_metadata,
            image_id=image_id,
            description=description,
            prompt=prompt,
            analysis=analysis,
            model=model,
        )
    return AssistantGalleryImageResponse(
        image_id=image_id,
        description=description,
        prompt=prompt,
        analysis=analysis,
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


async def _describe_gallery_image(image_id: str) -> AssistantGalleryImageResponse:
    _entry, _preview, data, model, duration_ms = await _assistant_vision_json(
        image_id,
        system_prompt="You describe a local gallery image concisely. Return only the useful visual description.",
        schema={"description": "string", "warnings": ["string"]},
        include_stored_prompt=False,
        max_tokens=350,
        temperature=0.15,
    )
    return AssistantGalleryImageResponse(
        image_id=image_id,
        description=_clamp_text(data.get("description"), 2000),
        prompt="",
        analysis={},
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


async def _prompt_gallery_image(
    image_id: str,
    *,
    target_language: Literal["en", "zh-CN"] | None = None,
    runtime: AssistantRuntime | None = None,
) -> AssistantGalleryImageResponse:
    _entry, _preview, data, model, duration_ms = await _assistant_vision_json(
        image_id,
        system_prompt=_image_prompt_system_prompt(target_language),
        schema={"prompt": "string", "warnings": ["string"]},
        include_stored_prompt=False,
        max_tokens=550,
        temperature=0.2,
        runtime=runtime,
    )
    prompt = _clamp_text(data.get("prompt"), MAX_PROMPT_CHARS)
    if not prompt:
        raise UpstreamError("AI Assistant returned an empty image prompt")
    return AssistantGalleryImageResponse(
        image_id=image_id,
        description="",
        prompt=prompt,
        analysis={},
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


def _assistant_metadata_response(image_id: str, row: dict[str, Any] | None) -> AssistantGalleryMetadataResponse:
    if not row:
        return AssistantGalleryMetadataResponse(image_id=image_id)
    return AssistantGalleryMetadataResponse(
        image_id=image_id,
        description=str(row.get("description") or ""),
        prompt=str(row.get("prompt") or ""),
        analysis=row.get("analysis") if isinstance(row.get("analysis"), dict) else {},
        model=str(row.get("model") or ""),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


async def get_gallery_metadata(image_id: str):
    entry = await run_db_operation(get_gallery_entry, image_id)
    if not entry:
        raise NotFoundError("Gallery entry not found")
    row = await run_db_operation(get_gallery_ai_metadata, image_id)
    return _assistant_metadata_response(image_id, row)


async def describe_gallery_image(image_id: str):
    return await _describe_gallery_image(image_id)


async def prompt_gallery_image(
    image_id: str,
    target_language: Literal["en", "zh-CN"] = "en",
):
    runtime = await _resolve_runtime_async(vision=True)
    return await _prompt_gallery_image(image_id, target_language=target_language, runtime=runtime)


async def analyze_gallery_image(
    image_id: str,
    target_language: Literal["en", "zh-CN"] = "en",
):
    runtime = await _resolve_runtime_async(vision=True)
    return await _analyze_gallery_image(image_id, persist=True, target_language=target_language, runtime=runtime)


__all__ = [name for name in globals() if not name.startswith("__")]
