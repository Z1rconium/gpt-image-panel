"""Default API settings shared by persistence and runtime configuration."""

from . import secrets
from . import settings as config
from .validators import get_env_var_ref_name


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
    }


def coerce_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
