"""Submit/poll/result driver for declaratively mapped ("async_json") providers.

The preset's ``provider_config`` describes the wire format; this module only
executes it. Follow-up URLs (status, result, cancel) come out of upstream
responses, so they are untrusted: they must stay on the preset's origin, because
the API key is sent along with them, and they pass the same SSRF checks as the
submit URL.
"""

import asyncio
import re
import time
from typing import Any

import aiohttp
from pydantic import ValidationError

from ...core import secrets
from ...core import settings as config
from ...core import validators as ssrf
from ...core.provider_mapping import (
    ProviderMappingError,
    render_template,
    select_all,
    select_first,
)
from ...core.redaction import redact_sensitive_text
from ...schemas.generation import GenerateRequest
from ...schemas.provider import ProviderConfig
from ..session_pool import TIMEOUT_UPSTREAM, get_pool
from .errors import UpstreamApiError, _warn_if_socks5_upstream_resolves_private
from .payloads import sent_generation_prompt
from .transport import ProgressCallback, parse_upstream_json_response

_MODEL_IN_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,199}$")
_MODEL_PLACEHOLDER = re.compile(r"\{\{\s*model\s*\}\}")
_CANCEL_TIMEOUT_SECONDS = 10.0
_MAX_POLL_DELAY_SECONDS = 15.0
_PROVIDER_LABEL = "async provider"


class _PollTimeout(Exception):
    pass


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _monotonic() -> float:
    return time.monotonic()


def load_provider_config(raw: Any) -> ProviderConfig:
    try:
        return ProviderConfig.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise UpstreamApiError(
            f"Invalid provider_config ({location}): {first.get('msg', 'invalid value')}"
        ) from exc


def _size_variables(payload: GenerateRequest) -> dict[str, Any]:
    if payload.size == "auto":
        return {}
    width_text, height_text = payload.size.lower().split("x", 1)
    return {"size": payload.size, "width": int(width_text), "height": int(height_text)}


def build_template_variables(payload: GenerateRequest, *, prompt_guard: bool) -> dict[str, Any]:
    return {
        "prompt": sent_generation_prompt(payload, prompt_guard=prompt_guard),
        "model": payload.model,
        "n": payload.n,
        "quality": payload.quality,
        "output_format": payload.output_format,
        "background": payload.background,
        **_size_variables(payload),
    }


def render_submit_path(path: str, model: str) -> str:
    if not _MODEL_PLACEHOLDER.search(path):
        return path
    if not _MODEL_IN_PATH.match(model) or ".." in model or "//" in model:
        raise UpstreamApiError(
            "Model name cannot be used in the provider URL path; use letters, digits, . _ - and /"
        )
    return _MODEL_PLACEHOLDER.sub(model, path)


def _auth_headers(cfg: ProviderConfig, api_key: str) -> dict[str, str]:
    value = f"{cfg.auth.scheme} {api_key}".strip()
    return {
        cfg.auth.header: value,
        "User-Agent": "opencode",
        "Content-Type": "application/json",
    }


async def _validated_upstream_url(url: str) -> str:
    try:
        await ssrf.validate_upstream_url_async(url, config.UPSTREAM_HOST_ALLOWLIST)
    except ValueError as exc:
        raise UpstreamApiError(redact_sensitive_text(f"Provider URL rejected: {exc}")) from exc
    return url


async def _followup_url(source: Any, path: str, label: str, api_url: str) -> str:
    value = select_first(source, path)
    if not isinstance(value, str) or not value.strip():
        raise UpstreamApiError(f"Provider response did not include a {label} URL at {path}")
    value = value.strip()
    if not secrets.same_origin(api_url, value):
        # The API key travels with these requests, so never follow another origin.
        raise UpstreamApiError(
            f"Provider {label} URL must be on the same origin as the preset API URL"
        )
    return await _validated_upstream_url(value)


async def _request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    socks5_proxy: str | None,
    json_body: Any = None,
) -> dict[str, Any]:
    request = getattr(session, method.lower())
    kwargs: dict[str, Any] = {"headers": headers, "allow_redirects": False}
    if json_body is not None:
        kwargs["json"] = json_body
    async with request(url, **kwargs) as resp:
        if not socks5_proxy:
            ssrf.validate_response_peer_ip(resp, "Upstream API")
        result, _preview = await parse_upstream_json_response(resp, _PROVIDER_LABEL, None)
    return result


async def _cancel_remote(
    session: aiohttp.ClientSession,
    cfg: ProviderConfig,
    submit_result: dict[str, Any],
    *,
    api_url: str,
    headers: dict[str, str],
    socks5_proxy: str | None,
) -> None:
    """Best effort: a failed cancel must never mask the original error."""
    if cfg.cancel is None:
        return
    try:
        cancel_url = await _followup_url(submit_result, cfg.cancel.url_path, "cancel", api_url)
        await asyncio.wait_for(
            _request_json(
                session,
                cfg.cancel.method,
                cancel_url,
                headers=headers,
                socks5_proxy=socks5_proxy,
            ),
            timeout=_CANCEL_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - cleanup only
        return


def _image_items(source: Any, cfg: ProviderConfig) -> list[dict[str, Any]]:
    key = "url" if cfg.result.image_kind == "url" else "b64_json"
    items = []
    for value in select_all(source, cfg.result.images_path):
        if isinstance(value, str) and value.strip():
            items.append({key: value.strip()})
    return items


async def run_async_provider(
    *,
    api_url: str,
    api_key: str,
    provider_config: Any,
    payload: GenerateRequest,
    progress: ProgressCallback | None,
    socks5_proxy: str | None,
    prompt_guard: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Run one task. Returns the image entries and a short response preview."""
    cfg = load_provider_config(provider_config)
    try:
        body = render_template(cfg.submit.body, build_template_variables(payload, prompt_guard=prompt_guard))
    except ProviderMappingError as exc:
        raise UpstreamApiError(f"Cannot build provider request: {exc}") from exc

    submit_path = render_submit_path(cfg.submit.path, payload.model)
    submit_url = await _validated_upstream_url(str(api_url).rstrip("/") + submit_path)
    await _warn_if_socks5_upstream_resolves_private(submit_url, socks5_proxy)

    headers = _auth_headers(cfg, api_key)
    session = get_pool().get(timeout_kind=TIMEOUT_UPSTREAM, socks5_proxy=socks5_proxy)

    if progress:
        progress("submitting_provider_task", "Submitting task to provider")
    submit_result = await _request_json(
        session, "POST", submit_url, headers=headers, socks5_proxy=socks5_proxy, json_body=body
    )
    status_url = await _followup_url(submit_result, cfg.poll.url_path, "status", api_url)

    deadline = _monotonic() + cfg.poll.timeout_seconds
    delay = cfg.poll.interval_seconds
    poll_result: dict[str, Any] = {}
    try:
        while True:
            poll_result = await _request_json(
                session, "GET", status_url, headers=headers, socks5_proxy=socks5_proxy
            )
            raw_status = select_first(poll_result, cfg.poll.status_path)
            status = "" if raw_status is None else str(raw_status)
            if status in cfg.poll.done:
                break
            if status in cfg.poll.failed:
                raise UpstreamApiError(
                    redact_sensitive_text(f"Provider task failed with status {status}")
                )
            if _monotonic() >= deadline:
                raise _PollTimeout()
            if progress:
                progress("polling_provider", f"Provider task status: {status or 'unknown'}")
            await _sleep(delay)
            delay = min(delay * 1.25, max(cfg.poll.interval_seconds, _MAX_POLL_DELAY_SECONDS))
    except _PollTimeout:
        await _cancel_remote(
            session, cfg, submit_result, api_url=api_url, headers=headers, socks5_proxy=socks5_proxy
        )
        raise UpstreamApiError(
            f"Provider task did not finish within {cfg.poll.timeout_seconds} seconds"
        ) from None
    except asyncio.CancelledError:
        await _cancel_remote(
            session, cfg, submit_result, api_url=api_url, headers=headers, socks5_proxy=socks5_proxy
        )
        raise

    source: Any = poll_result
    if cfg.result.url_path:
        result_path = cfg.result.url_path
        holder = submit_result if select_first(submit_result, result_path) is not None else poll_result
        result_url = await _followup_url(holder, result_path, "result", api_url)
        if progress:
            progress("fetching_provider_result", "Fetching provider result")
        source = await _request_json(
            session, "GET", result_url, headers=headers, socks5_proxy=socks5_proxy
        )

    items = _image_items(source, cfg)
    if not items:
        raise UpstreamApiError("Provider result did not include any images")
    return items, f"{_PROVIDER_LABEL} result: {len(items)} image(s)"
