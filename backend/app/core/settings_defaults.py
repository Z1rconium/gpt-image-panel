"""Default API settings shared by persistence and runtime configuration."""

from . import secrets
from . import settings as config
from .secrets import get_env_var_ref_name


def default_secret_reference(secret_id: str, value: str | None) -> str:
    if secret_id in secrets.configured_secret_ids():
        return secret_id
    env_var = get_env_var_ref_name(str(value or "").strip())
    return f"${{{env_var}}}" if env_var else ""


def default_prompt_optimizer_settings() -> dict:
    return {
        "enabled": config.PROMPT_OPTIMIZER_ENABLED,
        "api_url": config.PROMPT_OPTIMIZER_API_URL,
        "api_key": default_secret_reference(
            "builtin-prompt-optimizer-key",
            config.PROMPT_OPTIMIZER_API_KEY,
        ),
        "model": config.PROMPT_OPTIMIZER_MODEL,
        "timeout_seconds": config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS,
    }


def default_ai_assistant_settings() -> dict:
    return {
        "enabled": config.AI_ASSISTANT_ENABLED,
        "vision_model": config.AI_ASSISTANT_VISION_MODEL or config.PROMPT_OPTIMIZER_MODEL,
        "agent_enabled": False,
        "agent_model": "",
        "agent_max_tool_rounds": 4,
        "agent_system_prompt": "",
        "agent_web_search_enabled": False,
        "agent_web_search_supported": False,
    }


def coerce_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_prompt_optimizer_settings(
    raw: dict | None,
    *,
    api_key_normalizer: object = None,
) -> dict:
    defaults = default_prompt_optimizer_settings()
    if not isinstance(raw, dict):
        return defaults
    api_key = raw.get("api_key")
    if callable(api_key_normalizer):
        api_key = api_key_normalizer(api_key)
    else:
        api_key = str(api_key or "").strip()
    return {
        "enabled": coerce_bool(raw.get("enabled"), defaults["enabled"]),
        "api_url": str(raw.get("api_url") or "").strip(),
        "api_key": api_key,
        "model": str(raw.get("model") or defaults["model"]).strip() or defaults["model"],
        "timeout_seconds": coerce_positive_int(
            raw.get("timeout_seconds"),
            defaults["timeout_seconds"],
        ),
    }


def normalize_ai_assistant_settings(raw: dict | None) -> dict:
    defaults = default_ai_assistant_settings()
    if not isinstance(raw, dict):
        return defaults
    vision_model = (
        str(raw.get("vision_model") or defaults["vision_model"] or config.PROMPT_OPTIMIZER_MODEL).strip()
        or config.PROMPT_OPTIMIZER_MODEL
    )
    return {
        "enabled": coerce_bool(raw.get("enabled"), defaults["enabled"]),
        "vision_model": vision_model,
        "agent_enabled": coerce_bool(raw.get("agent_enabled"), defaults["agent_enabled"]),
        "agent_model": str(raw.get("agent_model") or "").strip()[:200],
        "agent_max_tool_rounds": min(
            max(1, coerce_positive_int(raw.get("agent_max_tool_rounds"), defaults["agent_max_tool_rounds"])),
            config.AGENT_MAX_TOOL_ROUNDS_CEILING,
        ),
        "agent_system_prompt": str(raw.get("agent_system_prompt") or "").strip()[:4000],
        "agent_web_search_enabled": bool(raw.get("agent_web_search_enabled", False)),
        "agent_web_search_supported": bool(raw.get("agent_web_search_supported", False)),
    }
