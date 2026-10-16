import asyncio

from fastapi import APIRouter, HTTPException

from ...repositories.db.execution import run_db_operation
from ...runtime.state import state
from ...services.presets import (
    apply_api_preset,
    apply_ai_assistant_settings,
    apply_preset_provider,
    apply_prompt_optimizer_settings,
    apply_r2_backup_settings,
    apply_nodeimage_settings,
    apply_upstream_socks5_proxy,
    apply_webhook_url,
    begin_api_settings_write,
    build_settings_response,
    end_api_settings_write,
    get_active_preset,
    get_ai_assistant_settings,
    get_prompt_optimizer_settings,
    get_r2_backup_settings,
    get_nodeimage_settings,
    get_preset_by_id,
    get_upstream_socks5_proxy,
    get_webhook_url,
    load_api_settings,
    mask_socks5_proxy_url,
    mask_webhook_url,
    persist_api_settings,
)
from ...core import settings as config
from ...core import secrets
from ...core import overall_config
from ...core import validators as ssrf
from ...core.api_paths import (
    normalize_api_path,
    normalize_default_model,
    normalize_default_response_format,
)
from ...integrations.r2.client import probe_r2_settings
from ...repositories.settings import (
    save_ai_assistant_settings,
    save_prompt_optimizer_settings,
    save_r2_backup_settings,
    save_nodeimage_settings,
)
from ...repositories.db import (
    list_overall_config_values,
    save_overall_config_overrides,
)
from ...schemas.overall_config import (
    OverallConfigItem,
    OverallConfigResponse,
    OverallConfigUpdateRequest,
)
from ...schemas.provider_mapping import (
    ProviderMappingPromptResponse,
    ProviderMappingValidateRequest,
    ProviderMappingValidateResponse,
)
from ...schemas.settings import (
    R2BackupSettingsRequest,
    R2HealthResponse,
    SettingsRequest,
    SettingsResponse,
)
from ...services.provider_mapping_assist import (
    build_mapping_prompt,
    validate_provider_mapping_request,
)
from .settings_bindings import (
    validate_nodeimage_secret_binding,
    validate_optimizer_secret_binding,
    validate_preset_secret_binding,
    validate_r2_secret_bindings,
)


router = APIRouter()


def _validate_webhook_security() -> None:
    webhook_url = get_webhook_url()
    if webhook_url and len(config.WEBHOOK_SIGNING_SECRET.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=422,
            detail="Webhook signing secret must contain at least 32 bytes",
        )


def _mask_overall_config_value(value: str, *, secret: bool) -> str:
    if not secret:
        return value
    return overall_config.MASKED_OVERALL_SECRET_VALUE if value else ""


def _serialize_overall_config(
    rows: dict[str, dict],
    *,
    restart_required_names: list[str] | None = None,
) -> OverallConfigResponse:
    items: list[OverallConfigItem] = []
    for spec in overall_config.OVERALL_CONFIG_REGISTRY:
        if spec.exposed_in_settings or not spec.exposed_in_overall_config:
            continue
        row = rows.get(spec.name, {})
        raw_value, source = overall_config.effective_value(spec, row)
        typed = overall_config.typed_value(spec, raw_value)
        env_value = str(row.get("env_value") or "")
        override_value = row.get("override_value")
        items.append(
            OverallConfigItem(
                name=spec.name,
                type=spec.type,
                group=spec.group,
                description=spec.description,
                value=overall_config.MASKED_OVERALL_SECRET_VALUE if spec.secret and raw_value else typed,
                value_masked=_mask_overall_config_value(raw_value, secret=spec.secret),
                env_value_masked=_mask_overall_config_value(env_value, secret=spec.secret),
                override_value_masked=(
                    _mask_overall_config_value(str(override_value or ""), secret=spec.secret)
                    if override_value is not None
                    else None
                ),
                source=source,
                is_env_set=bool(row.get("is_env_set")),
                has_override=override_value is not None,
                secret=spec.secret,
                hot_reload=spec.hot_reload and not spec.restart_required and not spec.build_only,
                restart_required=spec.restart_required,
                build_only=spec.build_only,
                startup_only=spec.startup_only,
                updated_at=row.get("updated_at"),
                override_updated_at=row.get("override_updated_at"),
            )
        )
    return OverallConfigResponse(
        items=items,
        restart_required_names=restart_required_names or [],
    )


@router.get("/api/settings/overall-config", response_model=OverallConfigResponse)
async def get_overall_config():
    rows = await run_db_operation(list_overall_config_values)
    return _serialize_overall_config(rows)


@router.put("/api/settings/overall-config", response_model=OverallConfigResponse)
async def update_overall_config(req: OverallConfigUpdateRequest):
    current_rows = await run_db_operation(list_overall_config_values)
    updates: dict[str, str | None] = {}
    seen_names: set[str] = set()
    for item in req.updates:
        if item.name in seen_names:
            raise HTTPException(status_code=422, detail=f"Duplicate config name: {item.name}")
        seen_names.add(item.name)
        spec = overall_config.OVERALL_CONFIG_BY_NAME.get(item.name)
        if spec is None or spec.exposed_in_settings or not spec.exposed_in_overall_config:
            raise HTTPException(status_code=422, detail=f"Unknown config name: {item.name}")
        if spec.startup_only:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name} can only be configured at process startup",
            )

        if item.clear_override:
            updates[item.name] = None
            continue

        value = item.value
        if spec.secret and value == overall_config.MASKED_OVERALL_SECRET_VALUE:
            continue
        if value is None:
            raise HTTPException(status_code=422, detail=f"value is required for {item.name}")
        try:
            updates[item.name] = overall_config.normalize_secret_override(spec, str(value))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"{item.name}: {e}") from e

    projected_rows = {
        name: dict(row)
        for name, row in current_rows.items()
    }
    for name, value in updates.items():
        row = projected_rows.setdefault(
            name,
            {
                "name": name,
                "env_value": "",
                "override_value": None,
                "is_env_set": False,
                "updated_at": None,
                "override_updated_at": None,
            },
        )
        row["override_value"] = value
    try:
        overall_config.validate_effective_security(projected_rows)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    rows = await run_db_operation(save_overall_config_overrides, updates)
    overall_config.apply_rows_to_config(rows)
    restart_required_names = [
        name
        for name in updates
        if (
            overall_config.OVERALL_CONFIG_BY_NAME[name].restart_required
            or overall_config.OVERALL_CONFIG_BY_NAME[name].build_only
        )
    ]
    return _serialize_overall_config(rows, restart_required_names=restart_required_names)


@router.post("/api/settings", response_model=SettingsResponse)
async def update_settings(req: SettingsRequest):
    await run_db_operation(load_api_settings)
    preset = (
        get_preset_by_id(req.active_preset_id)
        if req.active_preset_id
        else get_active_preset()
    )
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    begin_api_settings_write()
    try:
        preset["name"] = (req.preset_name or preset.get("name") or "Untitled preset").strip()
        previous_api_url = str(preset.get("api_url") or "")
        next_api_url = req.api_url.rstrip("/")
        if preset.get("api_key") and not secrets.same_origin(previous_api_url, next_api_url):
            preset["api_key"] = ""
        preset["api_url"] = next_api_url
        if req.api_key is not None:
            preset["api_key"] = req.api_key.strip()
        preset["api_path"] = normalize_api_path(req.api_path)
        if req.default_model is not None:
            preset["default_model"] = normalize_default_model(
                req.default_model,
                preset["api_path"],
            )
        if req.default_response_format is not None:
            preset["default_response_format"] = normalize_default_response_format(
                req.default_response_format
            )
        if req.supports_mask is not None:
            preset["supports_mask"] = bool(req.supports_mask)
        if req.prompt_guard is not None:
            preset["prompt_guard"] = bool(req.prompt_guard)
        apply_preset_provider(preset, req.provider_kind, req.provider_config)
        if req.upstream_socks5_proxy is not None:
            current_proxy = get_upstream_socks5_proxy(raw=True)
            requested_proxy = req.upstream_socks5_proxy.strip()
            if current_proxy and requested_proxy == mask_socks5_proxy_url(current_proxy):
                state.upstream_socks5_proxy = current_proxy
            else:
                apply_upstream_socks5_proxy(requested_proxy)
        if req.webhook_url is not None:
            current_webhook_url = get_webhook_url(raw=True)
            requested_webhook_url = req.webhook_url.strip()
            if current_webhook_url and requested_webhook_url == mask_webhook_url(current_webhook_url):
                state.webhook_url = current_webhook_url
            else:
                apply_webhook_url(requested_webhook_url)
            _validate_webhook_security()
        validate_preset_secret_binding(preset)
        apply_api_preset(preset)
        await run_db_operation(persist_api_settings)
    finally:
        end_api_settings_write()
    if req.prompt_optimizer is not None:
        current_optimizer = get_prompt_optimizer_settings()
        updated_optimizer = apply_prompt_optimizer_settings(current_optimizer, req.prompt_optimizer)
        validate_optimizer_secret_binding(updated_optimizer)
        await run_db_operation(save_prompt_optimizer_settings, updated_optimizer)
    if req.ai_assistant is not None:
        current_assistant = get_ai_assistant_settings()
        updated_assistant = apply_ai_assistant_settings(current_assistant, req.ai_assistant)
        await run_db_operation(save_ai_assistant_settings, updated_assistant)
    if req.r2_backup is not None:
        current_r2 = get_r2_backup_settings()
        updated_r2 = apply_r2_backup_settings(current_r2, req.r2_backup)
        if updated_r2.get("endpoint_url"):
            try:
                await ssrf.validate_r2_endpoint_url_async(
                    updated_r2["endpoint_url"],
                    config.R2_ENDPOINT_HOST_ALLOWLIST,
                )
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
        validate_r2_secret_bindings(updated_r2)
        await run_db_operation(save_r2_backup_settings, updated_r2)
    if req.nodeimage is not None:
        current_nodeimage = get_nodeimage_settings()
        updated_nodeimage = apply_nodeimage_settings(
            current_nodeimage,
            req.nodeimage,
        )
        validate_nodeimage_secret_binding(updated_nodeimage)
        await run_db_operation(save_nodeimage_settings, updated_nodeimage)
    return await run_db_operation(build_settings_response)


@router.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    await run_db_operation(load_api_settings)
    return await run_db_operation(build_settings_response)


@router.post("/api/settings/r2/health", response_model=R2HealthResponse)
async def check_r2_settings_health(req: R2BackupSettingsRequest):
    current = await run_db_operation(get_r2_backup_settings)
    draft = apply_r2_backup_settings(current, req)
    if draft.get("endpoint_url"):
        try:
            await ssrf.validate_r2_endpoint_url_async(
                draft["endpoint_url"],
                config.R2_ENDPOINT_HOST_ALLOWLIST,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    validate_r2_secret_bindings(draft)
    if not req.use_credentials:
        return R2HealthResponse(
            status="warning",
            checks=[
                {
                    "name": "configuration",
                    "status": "ok",
                    "message": "R2 configuration and secret bindings are valid",
                },
                {
                    "name": "remote_probe",
                    "status": "warning",
                    "message": "Credentialed remote probe was not requested",
                },
            ],
        )
    result = await asyncio.to_thread(probe_r2_settings, draft)
    return R2HealthResponse(**result)


@router.get(
    "/api/settings/provider-mapping/prompt",
    response_model=ProviderMappingPromptResponse,
)
async def get_settings_provider_mapping_prompt():
    prompt = await run_db_operation(build_mapping_prompt)
    return ProviderMappingPromptResponse(prompt=prompt)


@router.post(
    "/api/settings/provider-mapping/validate",
    response_model=ProviderMappingValidateResponse,
)
async def validate_settings_provider_mapping(req: ProviderMappingValidateRequest):
    return await run_db_operation(validate_provider_mapping_request,
        req.provider_config,
        req.sample_submit,
        req.sample_poll,
        req.sample_result,
    )
