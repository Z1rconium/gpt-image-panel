"""AI Assistant JSON transport and response shaping (F-G6-1).

Split out of :mod:`app.services.assistant_runtime`, which now owns only endpoint
resolution. This module holds the Assistant request wrapper, the prompt
templates, and the text/secret truncation applied to model responses.
"""

import re
from typing import Any, Literal

from ..core.errors import DomainError, InvalidRequestError, UpstreamTimeoutError
from ..core.image_models import MAX_PROMPT_CHARS
from ..integrations import assistant_client
from .assistant_runtime import AssistantRuntime, _resolve_runtime_async
from .assistant_slots import _assistant_request_limit

IMAGE_PROMPT_SYSTEM_PROMPT = """You reconstruct one directly usable image-generation prompt from visible pixels.
Organize the prompt in this order when the information is present: subject identity and action; overall color palette and color relationships; artistic medium and rendering style; environment; composition and viewpoint; lighting; materials and surface qualities; reasonably inferable lens and depth of field; clearly legible text.
Give particular attention to the image's overall color language and artistic style. Describe dominant and accent colors, temperature, saturation, value range, contrast, color harmony, and how colors are distributed across the image. Characterize style through visible evidence such as medium, rendering technique, line quality, shape language, shading, texture, and level of detail, without attributing the work to an artist.
When a widely recognizable named character, mascot, or public figure is clearly supported by distinctive visible evidence, including characters from anime, manga, comics, games, film, or television, explicitly name the identity and, for fictional characters, the source work or franchise. Treat this grounded identification as part of the subject description, not as attribution of the input image's source. If the identity is uncertain, do not guess; describe the visible identifying features neutrally instead.
Use high information density. Faithfully preserve spatial relationships, scale, pose, framing, and visual emphasis.
The prompt field must contain only the single prompt in the requested language. Do not put analysis, reasoning, a title, Markdown, source attribution, or meta-language such as \"this image\" in that field.
Do not invent unseen brands, artist names, exact camera or lens settings, or background stories. Use neutral descriptions for details that cannot be determined confidently.
When preview metadata says source_has_alpha is true, preserve transparency in the reconstruction and do not infer an opaque matte background.
Do not create a separate negative prompt. Keep safety warnings out of the prompt and return them only in the warnings field."""

AI_ASSISTANT_TEXT_FIELD_LIMITS = {
    "summary": 1200,
    "rationale": 1200,
    "message": 1200,
    "suggestion": 1200,
    "angle": 1200,
    "title": 120,
    "description": 2000,
    "prompt": MAX_PROMPT_CHARS,
    "rewritten_prompt": MAX_PROMPT_CHARS,
    "edit_prompt": MAX_PROMPT_CHARS,
    "style": 500,
    "composition": 800,
    "lighting": 500,
}
AI_ASSISTANT_STRING_LIST_FIELD_LIMITS = {
    "warnings": (10, 500),
    "likely_causes": (8, 800),
    "recommended_actions": (8, 800),
    "source_requirements": (8, 800),
    "subjects": (12, 200),
    "colors": (12, 100),
}
SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "secret",
    "token",
    "password",
    "credential",
}
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(api[-_ ]?key|authorization|bearer|token|secret|password|cookie)\b"
    r"([^\n\r]{0,24})([:=]\s*| +)([^\s,;]+)"
)
BEARER_VALUE_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")


def _clamp_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _truncate_assistant_data(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            str(child_key): _truncate_assistant_data(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        item_limit, char_limit = AI_ASSISTANT_STRING_LIST_FIELD_LIMITS.get(key, (20, 1000))
        return [
            _truncate_assistant_data(item, key=key if isinstance(item, str) else "")
            for item in value[:item_limit]
        ][:item_limit] if not all(isinstance(item, str) for item in value) else [
            _clamp_text(item, char_limit) for item in value if _clamp_text(item, char_limit)
        ][:item_limit]
    if isinstance(value, str):
        return _clamp_text(value, AI_ASSISTANT_TEXT_FIELD_LIMITS.get(key, 2000))
    return value


def _warnings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clamp_text(item, 500) for item in value if _clamp_text(item, 500)][:10]


def _image_prompt_system_prompt(target_language: Literal["en", "zh-CN"] | None = None) -> str:
    if target_language == "zh-CN":
        language_instruction = "Write the prompt in Simplified Chinese."
    elif target_language == "en":
        language_instruction = "Write the prompt in English."
    else:
        language_instruction = "Use the language you would normally use for this API."
    return f"{IMAGE_PROMPT_SYSTEM_PROMPT}\n{language_instruction}"


def _string_list(value: Any, *, max_items: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clamp_text(item, 800) for item in value if _clamp_text(item, 800)][:max_items]


async def _request_assistant_json_truncated(
    runtime: AssistantRuntime,
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    image: dict[str, str | int | bool] | None = None,
    images: list[dict[str, str | int | bool]] | None = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], str, int]:
    try:
        data, model_used, duration_ms = await assistant_client.request_assistant_json(
            api_url=runtime.api_url,
            api_key=runtime.api_key,
            api_path=runtime.api_path,
            model=runtime.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            image=image,
            images=images,
            timeout_seconds=runtime.timeout_seconds,
            max_tokens=max_tokens,
            temperature=temperature,
            prevalidated_endpoint=runtime.endpoint,
        )
        return _truncate_assistant_data(data), model_used, duration_ms
    except assistant_client.AssistantTimeoutError as e:
        raise UpstreamTimeoutError(str(e)) from e
    except assistant_client.AssistantError as e:
        status_code = 400 if e.status == 400 else 502
        raise DomainError(str(e), status_code=status_code) from e
    except ValueError as e:
        raise InvalidRequestError(str(e)) from e


async def _assistant_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    vision: bool = False,
    image: dict[str, str | int | bool] | None = None,
    images: list[dict[str, str | int | bool]] | None = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
    runtime: AssistantRuntime | None = None,
) -> tuple[dict[str, Any], str, int]:
    runtime = runtime or await _resolve_runtime_async(vision=vision)
    async with _assistant_request_limit(runtime.timeout_seconds):
        return await _request_assistant_json_truncated(
            runtime,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            image=image,
            images=images,
            max_tokens=max_tokens,
            temperature=temperature,
        )


__all__ = [name for name in globals() if not name.startswith("__")]
