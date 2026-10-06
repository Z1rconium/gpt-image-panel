"""Freeze image-provider configuration while resolving credentials live."""

import copy
from typing import Any

from ..core.errors import UnprocessableRequestError
from ..core.secrets import same_origin


PRESET_CONFIG_FIELDS = (
    "id", "name", "api_url", "api_path", "default_model",
    "default_response_format", "provider_kind", "provider_config",
    "prompt_guard", "supports_mask",
)


def image_preset_snapshot(preset: dict[str, Any]) -> dict[str, Any]:
    """Copy configuration fields only, never API keys or resolved credentials."""
    return copy.deepcopy({key: preset.get(key) for key in PRESET_CONFIG_FIELDS})


def apply_image_preset_snapshot(preset: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("id") != preset.get("id"):
        raise UnprocessableRequestError("The image preset snapshot does not match the selected preset.")
    try:
        origin_matches = same_origin(str(preset.get("api_url") or ""), str(snapshot.get("api_url") or ""))
    except ValueError:
        origin_matches = False
    if not origin_matches:
        raise UnprocessableRequestError(
            "The image preset endpoint changed after this turn was accepted. Start a new turn to use it."
        )
    return {**preset, **image_preset_snapshot(snapshot)}
