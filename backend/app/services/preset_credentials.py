"""Stored-credential resolution and masking helpers (F-G5-1).

Split out of :mod:`app.services.presets`, which mixed four concerns. This module
owns the credential/secrets view: the masking applied to settings responses and
the resolution of ``${ENV}`` references and registry secrets into a usable key.
It depends only on core modules, so the preset and feature-settings layers can
sit on top of it.
"""

from ..core import secrets
from ..core.errors import UnprocessableRequestError
from ..core.secrets import (
    get_env_var_ref_name,
    resolve_env_var_ref,
)

MASKED_API_KEY_VALUE = "********"


def mask_key(key: str) -> str:
    """Return a fixed mask that never reveals any character of a stored secret.

    The previous first-4/last-4 form leaked eight characters of every stored
    plaintext key to the settings console; the frontend already normalizes the
    mask to this constant, so no round-trip behavior changes.
    """
    if not key:
        return "***"
    return MASKED_API_KEY_VALUE


def get_api_key_env_var(api_key: str) -> str | None:
    return get_env_var_ref_name(api_key)


def is_malformed_api_key_env_ref(api_key: str) -> bool:
    return "${" in str(api_key or "") or "}" in str(api_key or "")


def resolve_api_key(
    api_key: str,
    *,
    purpose: secrets.SecretPurpose,
    target_url: str,
) -> str:
    """Resolve an API key reference; ${ENV} is only read through a declared purpose/origin binding."""
    env_var = get_env_var_ref_name(api_key)
    if env_var:
        try:
            return secrets.resolve_env_ref(
                env_var, purpose=purpose, target_url=target_url, field_name="API Key"
            )
        except secrets.SecretRegistryError as exc:
            raise UnprocessableRequestError(str(exc)) from exc
    return resolve_env_var_ref(api_key)


def _resolve_secret_or_unprocessable(
    secret_id: str,
    *,
    purpose: secrets.SecretPurpose,
    target_url: str,
) -> str:
    """Resolve a registry secret, normalizing registry errors into a 422 domain error."""
    try:
        return secrets.resolve_secret(
            secret_id,
            purpose=purpose,
            target_url=target_url,
        )
    except secrets.SecretRegistryError as exc:
        raise UnprocessableRequestError(str(exc)) from exc


def api_key_response_fields(api_key: str) -> dict:
    value = str(api_key or "").strip()
    env_var = get_api_key_env_var(value)
    if env_var:
        return {
            "api_key_masked": value,
            "has_api_key": True,
            "api_key_source": "env",
            "api_key_env_var": env_var,
            "api_key_secret_id": None,
        }
    if value in secrets.configured_secret_ids():
        return {
            "api_key_masked": value,
            "has_api_key": True,
            "api_key_source": "registry",
            "api_key_env_var": None,
            "api_key_secret_id": value,
        }
    if value:
        return {
            "api_key_masked": mask_key(value),
            "has_api_key": True,
            "api_key_source": "stored",
            "api_key_env_var": None,
            "api_key_secret_id": None,
        }
    return {
        "api_key_masked": "***",
        "has_api_key": False,
        "api_key_source": "empty",
        "api_key_env_var": None,
        "api_key_secret_id": None,
    }


def secret_response_fields(value: str, prefix: str) -> dict:
    fields = api_key_response_fields(value)
    return {
        f"{prefix}_masked": fields["api_key_masked"],
        f"has_{prefix}": fields["has_api_key"],
        f"{prefix}_source": fields["api_key_source"],
        f"{prefix}_env_var": fields["api_key_env_var"],
        f"{prefix}_secret_id": fields["api_key_secret_id"],
    }


def get_effective_preset_api_key(preset: dict) -> str:
    secret_id = str(preset.get("api_key") or "").strip()
    if not secret_id:
        raise UnprocessableRequestError("API credential is not configured")
    env_var = get_api_key_env_var(secret_id)
    if secret_id not in secrets.configured_secret_ids():
        resolved_key = resolve_api_key(
            secret_id,
            purpose="upstream_api",
            target_url=str(preset.get("api_url") or ""),
        )
        if resolved_key:
            return resolved_key
        if env_var:
            raise UnprocessableRequestError(f"API Key environment variable {env_var} is not set or empty. Set it in the server environment.")
        raise UnprocessableRequestError("API credential is not configured")
    return _resolve_secret_or_unprocessable(
        secret_id,
        purpose="upstream_api",
        target_url=str(preset.get("api_url") or ""),
    )


__all__ = [name for name in globals() if not name.startswith("__")]
