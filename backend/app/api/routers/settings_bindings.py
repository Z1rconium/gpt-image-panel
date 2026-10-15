"""Secret-registry binding validation shared by the settings API routers.

The settings surface validates predeclared secret bindings before persisting a
preset, the user settings, or an R2 draft. Extracted from ``settings.py`` so the
settings, presets, and health routers share one implementation.
"""

from fastapi import HTTPException

from ...core import settings as config
from ...core import secrets


def validate_secret_binding_or_422(
    secret_id: str,
    *,
    purpose: secrets.SecretPurpose,
    target_url: str,
    host_allowlist: str,
) -> None:
    """Validate a registry secret's binding, raising HTTP 422 on a registry error.

    Empty values and non-registry references are ignored: only predeclared
    secret_ids carry a purpose/origin binding that can fail validation.
    """
    secret_id = str(secret_id or "").strip()
    if not secret_id or secret_id not in secrets.configured_secret_ids():
        return
    try:
        secrets.validate_secret_binding(
            secret_id,
            purpose=purpose,
            target_url=target_url,
            host_allowlist=host_allowlist,
        )
    except secrets.SecretRegistryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def validate_preset_secret_binding(preset: dict) -> None:
    validate_secret_binding_or_422(
        preset.get("api_key"),
        purpose="upstream_api",
        target_url=str(preset.get("api_url") or ""),
        host_allowlist=config.UPSTREAM_HOST_ALLOWLIST,
    )


def validate_optimizer_secret_binding(settings: dict) -> None:
    validate_secret_binding_or_422(
        settings.get("api_key"),
        purpose="prompt_optimizer",
        target_url=str(settings.get("api_url") or ""),
        host_allowlist=config.PROMPT_OPTIMIZER_HOST_ALLOWLIST,
    )


def validate_r2_secret_bindings(settings: dict) -> None:
    endpoint_url = str(settings.get("endpoint_url") or "")
    for field, purpose in (
        ("access_key_id", "r2_access_key_id"),
        ("secret_access_key", "r2_secret_access_key"),
    ):
        validate_secret_binding_or_422(
            settings.get(field),
            purpose=purpose,
            target_url=endpoint_url,
            host_allowlist=config.R2_ENDPOINT_HOST_ALLOWLIST,
        )


def validate_nodeimage_secret_binding(settings: dict) -> None:
    validate_secret_binding_or_422(
        settings.get("api_key"),
        purpose="nodeimage_api_key",
        target_url="https://api.nodeimage.com",
        host_allowlist="api.nodeimage.com",
    )
