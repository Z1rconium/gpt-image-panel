"""Prompt optimization with a generated preview image.

This is the *generation* path: it renders a trial image from the current prompt
and asks the assistant to compare it against the target. Read-only gallery
analysis lives in :mod:`assistant_vision`.
"""

import asyncio
import base64
import json
import logging
import math
import time
from typing import Literal

from ..repositories.db.execution import run_db_operation
from ..core.image_models import MAX_PROMPT_CHARS
from . import presets, vision_previews
from ..core.errors import (
    DomainError,
    InvalidRequestError,
    UnprocessableRequestError,
    UpstreamError,
    UpstreamTimeoutError,
)
from ..integrations import assistant_client
from ..integrations.upstream.errors import UpstreamApiError
from ..integrations.upstream.generation import call_image_generation_preview_api
from ..core.media import detect_image_format
from ..schemas.generation import GenerateRequest
from ..schemas.assistant import (
    AssistantImagePromptResponse,
    AssistantImagePromptOptimizeResponse,
    AssistantTemporaryImage,
)
from .assistant_json import (
    _assistant_json,
    _clamp_text,
    _image_prompt_system_prompt,
    _warnings,
)
from .assistant_runtime import _resolve_runtime_async


logger = logging.getLogger(__name__)


async def prompt_from_uploaded_image(
    *,
    image_bytes: bytes,
    filename: str,
    content_type: str,
    target_language: Literal["en", "zh-CN"],
):
    runtime = await _resolve_runtime_async(vision=True)
    try:
        preview = await vision_previews.prepare_preview(
            image_bytes,
            filename=filename or "image",
            content_type=content_type,
        )
    except assistant_client.AssistantError as e:
        raise DomainError(str(e), status_code=400 if e.status == 400 else 502) from e
    finally:
        del image_bytes

    data, model, duration_ms = await _assistant_json(
        system_prompt=_image_prompt_system_prompt(target_language),
        user_prompt=json.dumps(
            {
                "target_language": target_language,
                "preview": {key: preview[key] for key in ("bytes", "width", "height", "source_has_alpha")},
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        schema={"prompt": "string", "warnings": ["string"]},
        vision=True,
        image=preview,
        max_tokens=700,
        temperature=0.2,
        runtime=runtime,
    )
    prompt = _clamp_text(data.get("prompt"), MAX_PROMPT_CHARS)
    if not prompt:
        raise UpstreamError("AI Assistant returned an empty image prompt")
    return AssistantImagePromptResponse(
        prompt=prompt,
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


def calculate_prompt_preview_size(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Target image dimensions must be positive")
    aspect = max(width / height, height / width)
    if aspect > 3:
        raise ValueError("Target image aspect ratio must not exceed 3:1 for prompt optimization")

    target_pixels = 800_000
    ratio = width / height
    ideal_width = math.sqrt(target_pixels * ratio)
    ideal_height = math.sqrt(target_pixels / ratio)
    candidates: list[tuple[float, int, int]] = []
    base_width = max(16, round(ideal_width / 16) * 16)
    base_height = max(16, round(ideal_height / 16) * 16)
    for candidate_width in range(max(16, base_width - 32), base_width + 33, 16):
        for candidate_height in range(max(16, base_height - 32), base_height + 33, 16):
            candidate_aspect = max(
                candidate_width / candidate_height,
                candidate_height / candidate_width,
            )
            if candidate_aspect > 3:
                continue
            ratio_error = abs(math.log((candidate_width / candidate_height) / ratio))
            area_error = abs(candidate_width * candidate_height - target_pixels) / target_pixels
            candidates.append((ratio_error * 4 + area_error, candidate_width, candidate_height))
    if not candidates:
        raise ValueError("Could not calculate a supported preview size")
    _, preview_width, preview_height = min(candidates)
    return preview_width, preview_height


def _prompt_optimization_system_prompt(target_language: Literal["en", "zh-CN"]) -> str:
    language = "Simplified Chinese" if target_language == "zh-CN" else "English"
    return (
        "You compare two images for iterative image-prompt refinement. The first image is the target; "
        "the second is a trial generated from the current prompt. Identify only visible differences "
        "that can be addressed in an image generation prompt. Preserve prompt details that already "
        "match the target. Do not invent unseen facts or discuss hidden generation settings. Return a "
        f"concise comparison summary and the complete refined prompt in {language}."
    )


def _prompt_preview_generation_config() -> tuple[str, str, str, str | None, str | None]:
    active_preset = presets.get_active_preset()
    api_path = str(active_preset.get("api_path") or "").strip()
    if api_path != "/v1/images/generations":
        raise InvalidRequestError("Prompt optimization preview requires the active preset to use /v1/images/generations")
    api_url = str(active_preset.get("api_url") or "").strip()
    if not api_url:
        raise InvalidRequestError("Active image generation preset API URL is not configured")
    api_key = presets.get_effective_preset_api_key(active_preset)
    model = str(active_preset.get("default_model") or "").strip()
    if not model:
        raise InvalidRequestError("Active image generation preset model is not configured")
    response_format = str(active_preset.get("default_response_format") or "").strip() or None
    return api_url, api_key, model, response_format, presets.get_upstream_socks5_proxy() or None


def _generated_image_mime_type(image_bytes: bytes) -> str:
    image_format = detect_image_format(image_bytes)
    mime_types = {
        "avif": "image/avif",
        "bmp": "image/bmp",
        "gif": "image/gif",
        "heif": "image/heif",
        "ico": "image/x-icon",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "tiff": "image/tiff",
        "webp": "image/webp",
    }
    return mime_types.get(str(image_format or ""), "application/octet-stream")


async def optimize_uploaded_image_prompt(
    *,
    image_bytes: bytes,
    filename: str,
    content_type: str,
    prompt: str,
    target_language: Literal["en", "zh-CN"],
):
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise UnprocessableRequestError("prompt must not be empty")

    runtime = await _resolve_runtime_async(vision=True)
    api_url, api_key, model, response_format, socks5_proxy = await run_db_operation(
        _prompt_preview_generation_config
    )
    try:
        target_preview = await vision_previews.prepare_preview(
            image_bytes,
            filename=filename or "image",
            content_type=content_type,
        )
    except assistant_client.AssistantError as e:
        raise DomainError(str(e), status_code=400 if e.status == 400 else 502) from e
    finally:
        del image_bytes

    try:
        preview_width, preview_height = calculate_prompt_preview_size(
            int(target_preview.get("source_width") or target_preview["width"]),
            int(target_preview.get("source_height") or target_preview["height"]),
        )
    except ValueError as e:
        raise InvalidRequestError(str(e)) from e

    generation_payload = GenerateRequest(
        prompt=normalized_prompt,
        size=f"{preview_width}x{preview_height}",
        model=model,
        n=1,
        quality="low",
        output_format="png",
        response_format=response_format,
    )

    generation_started = time.monotonic()
    try:
        generated_bytes = await call_image_generation_preview_api(
            api_url,
            api_key,
            generation_payload,
            socks5_proxy=socks5_proxy,
        )
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise UpstreamTimeoutError("Prompt optimization preview generation timed out") from e
    except UpstreamApiError as e:
        raise UpstreamError(f"Prompt optimization preview generation failed: {e}") from e
    except ValueError as e:
        raise InvalidRequestError(f"Prompt optimization preview configuration is invalid: {e}") from e
    except Exception as e:
        logger.warning("Prompt optimization preview generation failed", exc_info=True)
        raise UpstreamError("Prompt optimization preview generation failed") from e
    generation_duration_ms = int((time.monotonic() - generation_started) * 1000)

    try:
        generated_preview = await vision_previews.prepare_preview(
            generated_bytes,
            filename="assistant-preview",
            content_type="",
        )
    except assistant_client.AssistantError as e:
        raise UpstreamError(f"Generated preview image is invalid: {e}") from e

    data, vision_model, comparison_duration_ms = await _assistant_json(
        system_prompt=_prompt_optimization_system_prompt(target_language),
        user_prompt=json.dumps(
            {
                "target_language": target_language,
                "current_prompt": normalized_prompt,
                "target_dimensions": [
                    target_preview.get("source_width", target_preview["width"]),
                    target_preview.get("source_height", target_preview["height"]),
                ],
                "trial_dimensions": [
                    generated_preview.get("source_width", generated_preview["width"]),
                    generated_preview.get("source_height", generated_preview["height"]),
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        schema={
            "comparison_summary": "string",
            "prompt": "string",
            "warnings": ["string"],
        },
        vision=True,
        images=[
            {**target_preview, "label": "Target image (first image)"},
            {**generated_preview, "label": "Trial generated image (second image)"},
        ],
        max_tokens=900,
        temperature=0.2,
        runtime=runtime,
    )
    optimized_prompt = _clamp_text(data.get("prompt"), MAX_PROMPT_CHARS)
    if not optimized_prompt:
        raise UpstreamError("AI Assistant returned an empty optimized prompt")
    comparison_summary = _clamp_text(data.get("comparison_summary"), 2000)
    if not comparison_summary:
        raise UpstreamError("AI Assistant returned an empty comparison summary")

    generated_width = int(generated_preview.get("source_width") or generated_preview["width"])
    generated_height = int(generated_preview.get("source_height") or generated_preview["height"])
    return AssistantImagePromptOptimizeResponse(
        prompt=optimized_prompt,
        comparison_summary=comparison_summary,
        warnings=_warnings(data.get("warnings")),
        model=vision_model,
        duration_ms=comparison_duration_ms,
        temporary_image=AssistantTemporaryImage(
            b64=base64.b64encode(generated_bytes).decode("ascii"),
            mime_type=_generated_image_mime_type(generated_bytes),
            width=generated_width,
            height=generated_height,
            model=model,
            duration_ms=generation_duration_ms,
        ),
    )


__all__ = [name for name in globals() if not name.startswith("__")]
