"""Settings-preset management routes (CRUD, import/export, activation)."""

import uuid

from fastapi import APIRouter, HTTPException

from ...repositories.db.execution import run_db_operation
from ...runtime.state import state
from ...services.preset_package import (
    apply_preset_import,
    build_preset_export,
    build_preset_import_preview,
)
from ...services.presets import (
    apply_api_preset,
    apply_preset_provider,
    begin_api_settings_write,
    build_settings_response,
    end_api_settings_write,
    get_active_preset,
    get_api_presets,
    get_preset_by_id,
    load_api_settings,
    persist_api_settings,
    reorder_api_presets,
)
from ...core import secrets
from ...core.api_paths import (
    normalize_api_path,
    normalize_default_model,
    normalize_default_response_format,
)
from ...schemas.presets import (
    PresetCreateRequest,
    PresetExportPackage,
    PresetExportRequest,
    PresetImportApplyRequest,
    PresetImportPreviewRequest,
    PresetImportPreviewResponse,
    PresetOrderRequest,
)
from ...schemas.settings import SettingsResponse
from .settings_bindings import validate_preset_secret_binding


router = APIRouter()


@router.post("/api/settings/presets", response_model=SettingsResponse)
async def create_settings_preset(req: PresetCreateRequest):
    await run_db_operation(load_api_settings)
    source = get_preset_by_id(req.source_preset_id) if req.source_preset_id else None
    source = source or get_active_preset()
    presets = get_api_presets()
    next_number = len(presets) + 1
    preset = {
        "id": uuid.uuid4().hex,
        "name": (req.name or f"Preset {next_number}").strip() or f"Preset {next_number}",
        "api_url": (
            req.api_url if req.api_url is not None else source.get("api_url", "")
        ).rstrip("/"),
        "api_key": (
            req.api_key.strip()
            if req.api_key is not None
            else source.get("api_key", "")
        ),
        "api_path": normalize_api_path(
            req.api_path or source.get("api_path", "/v1/images/generations")
        ),
    }
    if (
        req.api_key is None
        and source.get("api_key")
        and not secrets.same_origin(source.get("api_url"), preset["api_url"])
    ):
        preset["api_key"] = ""
    validate_preset_secret_binding(preset)
    preset["default_model"] = normalize_default_model(
        req.default_model if req.default_model is not None else source.get("default_model"),
        preset["api_path"],
    )
    preset["default_response_format"] = normalize_default_response_format(
        req.default_response_format
        if req.default_response_format is not None
        else source.get("default_response_format")
    )
    preset["supports_mask"] = (
        bool(req.supports_mask)
        if req.supports_mask is not None
        else bool(source.get("supports_mask", True))
    )
    preset["prompt_guard"] = (
        bool(req.prompt_guard)
        if req.prompt_guard is not None
        else bool(source.get("prompt_guard", False))
    )
    preset["provider_kind"] = source.get("provider_kind", "openai")
    preset["provider_config"] = source.get("provider_config")
    apply_preset_provider(preset, req.provider_kind, req.provider_config)
    begin_api_settings_write()
    try:
        presets.append(preset)
        apply_api_preset(preset)
        await run_db_operation(persist_api_settings)
    finally:
        end_api_settings_write()
    return await run_db_operation(build_settings_response)


@router.post("/api/settings/presets/export", response_model=PresetExportPackage)
async def export_settings_preset(req: PresetExportRequest):
    await run_db_operation(load_api_settings)
    return await run_db_operation(build_preset_export, req.preset_id)


@router.post(
    "/api/settings/presets/import/preview", response_model=PresetImportPreviewResponse
)
async def preview_settings_presets_import(req: PresetImportPreviewRequest):
    await run_db_operation(load_api_settings)
    return await run_db_operation(build_preset_import_preview, req.package)


@router.post("/api/settings/presets/import", response_model=SettingsResponse)
async def import_settings_presets(req: PresetImportApplyRequest):
    await run_db_operation(load_api_settings)
    await run_db_operation(apply_preset_import,
        req.package,
        [item.model_dump(mode="json") for item in req.items],
    )
    return await run_db_operation(build_settings_response)


@router.put("/api/settings/presets/order", response_model=SettingsResponse)
async def update_settings_preset_order(req: PresetOrderRequest):
    await run_db_operation(load_api_settings)
    await run_db_operation(reorder_api_presets, req.preset_ids)
    return await run_db_operation(build_settings_response)


@router.post("/api/settings/presets/{preset_id}/activate", response_model=SettingsResponse)
async def activate_settings_preset(preset_id: str):
    await run_db_operation(load_api_settings)
    preset = get_preset_by_id(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    begin_api_settings_write()
    try:
        apply_api_preset(preset)
        await run_db_operation(persist_api_settings)
    finally:
        end_api_settings_write()
    return await run_db_operation(build_settings_response)


@router.delete("/api/settings/presets/{preset_id}", response_model=SettingsResponse)
async def delete_settings_preset(preset_id: str):
    await run_db_operation(load_api_settings)
    presets = get_api_presets()
    if len(presets) <= 1:
        raise HTTPException(status_code=400, detail="At least one preset is required")

    delete_index = next(
        (index for index, preset in enumerate(presets) if preset["id"] == preset_id),
        None,
    )
    if delete_index is None:
        raise HTTPException(status_code=404, detail="Preset not found")

    begin_api_settings_write()
    try:
        active_preset_id = getattr(state, "active_preset_id", presets[0]["id"])
        presets.pop(delete_index)
        state.api_presets = presets
        if not any(preset["id"] == active_preset_id for preset in presets):
            fallback = presets[min(delete_index, len(presets) - 1)]
            apply_api_preset(fallback)
        else:
            state.active_preset_id = active_preset_id

        await run_db_operation(persist_api_settings)
    finally:
        end_api_settings_write()

    return await run_db_operation(build_settings_response)
