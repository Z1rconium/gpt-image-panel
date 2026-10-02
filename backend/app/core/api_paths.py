import json
from typing import Any

from . import settings as config

DEFAULT_API_PATH = "/v1/images/generations"
RESPONSES_API_PATH = "/v1/responses"
CHAT_COMPLETIONS_API_PATH = "/v1/chat/completions"
ALLOWED_API_PATHS = {DEFAULT_API_PATH, RESPONSES_API_PATH, CHAT_COMPLETIONS_API_PATH}
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_RESPONSE_FORMAT = "url"
ALLOWED_RESPONSE_FORMATS = {"", "url", "b64_json"}
PROVIDER_KIND_OPENAI = "openai"
PROVIDER_KIND_ASYNC_JSON = "async_json"
ALLOWED_PROVIDER_KINDS = {PROVIDER_KIND_OPENAI, PROVIDER_KIND_ASYNC_JSON}


def normalize_api_path(api_path: str | None) -> str:
    value = str(api_path or config.DEFAULT_API_PATH or DEFAULT_API_PATH)
    return value if value in ALLOWED_API_PATHS else DEFAULT_API_PATH


def default_model_for_api_path(api_path: str | None) -> str:
    normalized_api_path = normalize_api_path(api_path)
    if normalized_api_path == RESPONSES_API_PATH:
        responses_model = str(config.DEFAULT_RESPONSES_MODEL or "").strip()
        if responses_model:
            return responses_model
    return DEFAULT_IMAGE_MODEL


def normalize_default_model(default_model: str | None, api_path: str | None = None) -> str:
    value = str(default_model or "").strip()
    return value or default_model_for_api_path(api_path)


def normalize_default_response_format(response_format: str | None) -> str:
    if response_format is None:
        return DEFAULT_RESPONSE_FORMAT
    value = str(response_format).strip()
    return value if value in ALLOWED_RESPONSE_FORMATS else DEFAULT_RESPONSE_FORMAT


def build_upstream_url(api_url: str, api_path: str) -> str:
    base_url = str(api_url or "").rstrip("/")
    path = "/" + str(api_path or "").lstrip("/")

    if base_url.endswith(path):
        return base_url
    if base_url.endswith("/v1") and path.startswith("/v1/"):
        return f"{base_url}{path[3:]}"
    return f"{base_url}{path}"


def normalize_supports_mask(value: Any | None) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def normalize_prompt_guard(value: Any | None) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def normalize_provider_kind(value: Any | None) -> str:
    text = str(value or "").strip()
    return text if text in ALLOWED_PROVIDER_KINDS else PROVIDER_KIND_OPENAI


def normalize_provider_config(value: Any | None) -> dict[str, Any] | None:
    """Shape-only normalisation; ProviderConfig validates it on write and on use."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else None
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) and value else None


def normalize_api_preset(raw: dict[str, Any] | None, fallback_id: str = "default") -> dict[str, Any]:
    preset = raw if isinstance(raw, dict) else {}
    preset_id = str(preset.get("id") or fallback_id)
    api_path = normalize_api_path(str(preset.get("api_path") or config.DEFAULT_API_PATH))
    provider_kind = normalize_provider_kind(preset.get("provider_kind"))
    return {
        "id": preset_id,
        "name": str(preset.get("name") or "Untitled preset").strip() or "Untitled preset",
        "api_url": str(preset.get("api_url") or "").rstrip("/"),
        "api_key": str(preset.get("api_key") or "").strip(),
        "api_path": api_path,
        "default_model": normalize_default_model(preset.get("default_model"), api_path),
        "default_response_format": normalize_default_response_format(
            preset.get("default_response_format")
        ),
        "supports_mask": normalize_supports_mask(preset.get("supports_mask")),
        "prompt_guard": normalize_prompt_guard(preset.get("prompt_guard")),
        "provider_kind": provider_kind,
        "provider_config": normalize_provider_config(preset.get("provider_config")),
    }
