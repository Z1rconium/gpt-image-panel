"""Preset connectivity/health probing (distinct from settings CRUD)."""

from fastapi import APIRouter, Body, HTTPException

from ...core import validators as ssrf
from ...core.api_paths import (
    ALLOWED_API_PATHS,
    PROVIDER_KIND_ASYNC_JSON,
    build_upstream_url,
    normalize_provider_kind,
)
from ...integrations.upstream import async_provider, generation as proxy
from ...integrations.upstream import transport as transport_client
from ...schemas.presets import (
    CredentialProbeRequest,
    PresetHealthResponse,
)
from ...services.preset_credentials import get_effective_preset_api_key
from ...services.presets import (
    get_exception_message,
    get_preset_by_id,
)


router = APIRouter()

HEALTH_STATUS_RANK = {"ok": 0, "warning": 1, "error": 2}


def add_health_check(checks: list[dict], name: str, status: str, message: str):
    checks.append({"name": name, "status": status, "message": message})


def health_status(checks: list[dict]) -> str:
    if not checks:
        return "error"
    return max(
        (check["status"] for check in checks),
        key=lambda status: HEALTH_STATUS_RANK[status],
    )


def preset_health_status(checks: list[dict]) -> str:
    blocking_checks = [check for check in checks if check["name"] != "upstream_probe"]
    if not blocking_checks:
        return "ok"
    return health_status(blocking_checks)


async def validate_health_api_url(
    api_url: str,
    api_path: str,
    checks: list[dict],
) -> bool:
    if not api_url:
        add_health_check(checks, "api_url", "error", "API URL is not configured")
        return False

    try:
        normalized_api_url = ssrf.normalize_upstream_base_url(api_url)
    except ValueError as e:
        add_health_check(checks, "api_url", "error", str(e))
        return False

    try:
        await ssrf.validate_upstream_url_async(
            build_upstream_url(normalized_api_url, api_path),
        )
    except ValueError as e:
        add_health_check(checks, "ssrf", "error", str(e))
        return False

    add_health_check(
        checks,
        "ssrf",
        "ok",
        "API URL passed scheme, DNS, and private-IP checks",
    )
    return True


def validate_health_api_path(api_path: str, checks: list[dict]) -> bool:
    if api_path not in ALLOWED_API_PATHS:
        add_health_check(
            checks,
            "api_path",
            "error",
            (
                "API path is not supported. Allowed paths: "
                + ", ".join(sorted(ALLOWED_API_PATHS))
            ),
        )
        return False

    add_health_check(checks, "api_path", "ok", f"API path {api_path} is supported")
    return True


def validate_health_provider(preset: dict, checks: list[dict]) -> str | None:
    """Check an async_json mapping; returns the submit path to probe, or None."""
    try:
        provider = async_provider.load_provider_config(preset.get("provider_config"))
        submit_path = async_provider.render_submit_path(
            provider.submit.path,
            str(preset.get("default_model") or ""),
        )
    except proxy.UpstreamApiError as e:
        add_health_check(checks, "provider_config", "error", str(e))
        return None

    add_health_check(
        checks,
        "provider_config",
        "ok",
        "Async provider mapping is valid; no task was submitted",
    )
    return submit_path


def validate_health_api_key(api_key: str, checks: list[dict]) -> str:
    raw_key = str(api_key or "").strip()
    if raw_key:
        add_health_check(checks, "api_key", "ok", "A Secret Registry credential is configured")
        return raw_key

    add_health_check(checks, "api_key", "error", "API key is not configured")
    return ""


@router.post(
    "/api/settings/presets/{preset_id}/health",
    response_model=PresetHealthResponse,
)
async def check_settings_preset_health(
    preset_id: str,
    req: CredentialProbeRequest | None = Body(default=None),
):
    preset = get_preset_by_id(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    checks: list[dict] = []
    api_url = str(preset.get("api_url") or "").rstrip("/")
    api_path = str(preset.get("api_path") or "")

    if normalize_provider_kind(preset.get("provider_kind")) == PROVIDER_KIND_ASYNC_JSON:
        provider_path = validate_health_provider(preset, checks)
        api_path_ok = provider_path is not None
        api_path = provider_path or ""
    else:
        api_path_ok = validate_health_api_path(api_path, checks)
    url_ok = (
        await validate_health_api_url(api_url, api_path, checks)
        if api_path_ok
        else False
    )
    validate_health_api_key(preset.get("api_key", ""), checks)
    effective_api_key = get_effective_preset_api_key(preset) if req and req.use_credentials else ""

    if api_path_ok and url_ok:
        try:
            probe_result = await transport_client.probe_upstream_endpoint(
                api_url,
                api_path,
                effective_api_key,
            )
        except Exception as e:
            probe_result = {
                "status": "error",
                "message": f"Upstream probe failed: {get_exception_message(e)}",
            }
        add_health_check(
            checks,
            "upstream_probe",
            str(probe_result["status"]),
            str(probe_result["message"]),
        )
    else:
        add_health_check(
            checks,
            "upstream_probe",
            "warning",
            "Skipped upstream probe because local URL/path validation failed",
        )

    return PresetHealthResponse(status=preset_health_status(checks), checks=checks)
