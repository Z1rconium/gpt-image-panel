"""Preset export/import packages: build, validate, preview and apply."""

import json
import uuid
from typing import Any

from pydantic import ValidationError

from ..core import secrets
from ..core.api_paths import (
    PROVIDER_KIND_ASYNC_JSON,
    normalize_api_path,
    normalize_default_response_format,
    normalize_prompt_guard,
    normalize_provider_kind,
    normalize_supports_mask,
)
from ..core.errors import NotFoundError, UnprocessableRequestError
from ..core.utils import utc_now
from ..runtime.state import state
from ..schemas.provider import provider_capabilities
from ..schemas.settings import MAX_PRESET_IMPORT_BYTES, PresetExportPackage
from .presets import (
    _stored_provider_config,
    apply_api_preset,
    begin_api_settings_write,
    end_api_settings_write,
    get_api_presets,
    get_preset_by_id,
    persist_api_settings,
)


def _preset_export_item(preset: dict) -> dict:
    provider_config = _stored_provider_config(preset)
    return {
        "name": str(preset.get("name") or "Untitled preset"),
        "api_url": str(preset.get("api_url") or "").rstrip("/"),
        "api_path": normalize_api_path(preset.get("api_path")),
        "default_model": str(preset.get("default_model") or ""),
        "default_response_format": normalize_default_response_format(
            preset.get("default_response_format")
        ),
        "supports_mask": normalize_supports_mask(preset.get("supports_mask")),
        "prompt_guard": normalize_prompt_guard(preset.get("prompt_guard")),
        "provider_kind": normalize_provider_kind(preset.get("provider_kind")),
        "provider_config": (
            provider_config.model_dump(mode="json") if provider_config is not None else None
        ),
    }


def build_preset_export(preset_id: str) -> dict:
    """Build the secret-free export package for one preset."""
    preset = get_preset_by_id(preset_id)
    if not preset:
        raise NotFoundError("Preset not found")
    return {
        "format": "gpt-image-panel-presets",
        "format_version": 1,
        "exported_at": utc_now(),
        "presets": [_preset_export_item(preset)],
    }


def _package_errors(exc: ValidationError) -> list[dict]:
    issues: list[dict] = []
    for error in exc.errors()[:24]:
        location = ".".join(str(part) for part in error.get("loc", ()))
        issues.append(
            {"path": location or "package", "message": str(error.get("msg") or "invalid value")}
        )
    return issues


def validate_preset_import_package(package: Any) -> tuple[Any | None, list[dict], int]:
    """Validate a raw import package, returning `(package, errors, byte_size)`."""
    if isinstance(package, str):
        try:
            package = json.loads(package)
        except json.JSONDecodeError as exc:
            return None, [{"path": "package", "message": f"not valid JSON: {exc.msg}"}], len(
                package.encode("utf-8")
            )
    if not isinstance(package, dict):
        return None, [{"path": "package", "message": "expected a JSON object"}], 0
    size = len(json.dumps(package, ensure_ascii=False).encode("utf-8"))
    if size > MAX_PRESET_IMPORT_BYTES:
        return (
            None,
            [
                {
                    "path": "package",
                    "message": f"package is too large ({size} bytes; max {MAX_PRESET_IMPORT_BYTES})",
                }
            ],
            size,
        )
    try:
        parsed = PresetExportPackage.model_validate(package)
    except ValidationError as exc:
        return None, _package_errors(exc), size
    issues: list[dict] = []
    for index, item in enumerate(parsed.presets):
        if item.provider_kind == PROVIDER_KIND_ASYNC_JSON and item.provider_config is None:
            issues.append(
                {
                    "path": f"presets.{index}.provider_config",
                    "message": "provider_config is required when provider_kind is async_json",
                }
            )
    if issues:
        return None, issues, size
    return parsed, [], size


def build_preset_import_preview(package: Any) -> dict:
    parsed, errors, size = validate_preset_import_package(package)
    if parsed is None:
        return {"valid": False, "errors": errors, "items": [], "total_bytes": size}
    existing = get_api_presets()
    items = []
    for index, item in enumerate(parsed.presets):
        duplicate = next(
            (
                preset
                for preset in existing
                if str(preset.get("name") or "").strip().lower() == item.name.strip().lower()
            ),
            None,
        )
        will_reuse_key = bool(
            duplicate and secrets.same_origin(duplicate.get("api_url"), item.api_url)
        )
        warnings = []
        if not will_reuse_key:
            warnings.append("The API key is not included; enter it after importing.")
        if item.provider_kind == PROVIDER_KIND_ASYNC_JSON:
            warnings.append("Async providers support generation only.")
        items.append(
            {
                "index": index,
                "name": item.name,
                "api_url": item.api_url,
                "provider_kind": item.provider_kind,
                "duplicate_preset_id": str(duplicate.get("id")) if duplicate else None,
                "duplicate_preset_name": str(duplicate.get("name")) if duplicate else None,
                "will_reuse_api_key": will_reuse_key,
                "warnings": warnings,
            }
        )
    return {"valid": True, "errors": [], "items": items, "total_bytes": size}


def _new_preset_from_export(item: Any) -> dict:
    preset = {
        "id": uuid.uuid4().hex,
        "name": item.name,
        "api_url": str(item.api_url).rstrip("/"),
        "api_key": "",
        "api_path": normalize_api_path(item.api_path),
        "default_model": str(item.default_model or ""),
        "default_response_format": normalize_default_response_format(
            item.default_response_format
        ),
        "supports_mask": normalize_supports_mask(item.supports_mask),
        "prompt_guard": normalize_prompt_guard(item.prompt_guard),
        "provider_kind": normalize_provider_kind(item.provider_kind),
        "provider_config": (
            item.provider_config.model_dump(mode="json")
            if item.provider_config is not None
            else None
        ),
    }
    if preset["provider_kind"] == PROVIDER_KIND_ASYNC_JSON:
        preset["supports_mask"] = provider_capabilities(preset["provider_config"]).mask
    return preset


def _merge_preset_from_export(target: dict, item: Any) -> None:
    same_origin = secrets.same_origin(target.get("api_url"), item.api_url)
    target["name"] = item.name
    target["api_url"] = str(item.api_url).rstrip("/")
    target["api_path"] = normalize_api_path(item.api_path)
    target["default_model"] = str(item.default_model or "")
    target["default_response_format"] = normalize_default_response_format(
        item.default_response_format
    )
    target["supports_mask"] = normalize_supports_mask(item.supports_mask)
    target["prompt_guard"] = normalize_prompt_guard(item.prompt_guard)
    target["provider_kind"] = normalize_provider_kind(item.provider_kind)
    target["provider_config"] = (
        item.provider_config.model_dump(mode="json")
        if item.provider_config is not None
        else None
    )
    if target["provider_kind"] == PROVIDER_KIND_ASYNC_JSON:
        target["supports_mask"] = provider_capabilities(target["provider_config"]).mask
    if not same_origin:
        # Never carry a stored key across origins.
        target["api_key"] = ""


def apply_preset_import(package: Any, instructions: list[dict]) -> None:
    """Apply an import atomically; every validation happens before any write."""
    parsed, errors, _size = validate_preset_import_package(package)
    if parsed is None:
        detail = "; ".join(f"{issue['path']}: {issue['message']}" for issue in errors[:3])
        raise UnprocessableRequestError(detail or "Invalid preset import package")

    presets = get_api_presets()
    by_id = {str(preset.get("id")): preset for preset in presets}
    actions: dict[int, dict] = {}
    for entry in instructions:
        index = int(entry.get("index"))
        if index in actions:
            raise UnprocessableRequestError(
                f"Duplicate import instruction for preset #{index + 1}"
            )
        if index < 0 or index >= len(parsed.presets):
            raise UnprocessableRequestError("Import instruction refers to an unknown preset")
        actions[index] = entry

    updates: dict[str, int] = {}
    for index, entry in actions.items():
        if str(entry.get("action") or "create") != "update":
            continue
        target_id = str(entry.get("target_preset_id") or "")
        if target_id not in by_id:
            raise UnprocessableRequestError("Import update target was not found")
        if target_id in updates:
            raise UnprocessableRequestError("Two import items cannot update the same preset")
        updates[target_id] = index

    new_presets = [dict(preset) for preset in presets]
    for index, item in enumerate(parsed.presets):
        action = str(actions.get(index, {}).get("action") or "create")
        if action == "skip":
            continue
        if action == "update":
            target_id = str(actions[index].get("target_preset_id") or "")
            position = next(
                position
                for position, preset in enumerate(new_presets)
                if str(preset.get("id")) == target_id
            )
            target = dict(new_presets[position])
            _merge_preset_from_export(target, item)
            new_presets[position] = target
        else:
            new_presets.append(_new_preset_from_export(item))

    previous_presets = list(state.api_presets or [])
    previous_active_id = str(getattr(state, "active_preset_id", "") or "")
    begin_api_settings_write()
    try:
        state.api_presets = new_presets
        for target_id in updates:
            if target_id == previous_active_id:
                updated = next(
                    preset for preset in new_presets if str(preset.get("id")) == target_id
                )
                apply_api_preset(updated)
                break
        persist_api_settings()
    except Exception:
        state.api_presets = previous_presets
        if previous_active_id:
            previous = next(
                (
                    preset
                    for preset in previous_presets
                    if str(preset.get("id")) == previous_active_id
                ),
                None,
            )
            if previous is not None:
                apply_api_preset(previous)
        raise
    finally:
        end_api_settings_write()
