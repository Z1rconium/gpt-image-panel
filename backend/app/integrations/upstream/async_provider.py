"""Submit/poll/result driver for declaratively mapped ("async_json") providers.

The preset's ``provider_config`` describes the wire format; this module only
executes it. Follow-up URLs (status, result, cancel) are either read from
upstream responses or rendered from an upstream task id, so they are untrusted:
they must stay on the preset's origin, because the API key is sent along with
them, and they pass the same SSRF checks as the submit URL.

When a caller passes a ``checkpoint`` callback, the driver persists the remote
task state (task id, follow-up URLs, absolute deadline, idempotency key) under
the unit's claim fence. A reclaimed unit can then resume polling instead of
submitting the task again.
"""

import asyncio
import json
import re
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlencode

import aiohttp
from pydantic import ValidationError

from ...core import secrets
from ...core import settings as config
from ...core import validators as ssrf
from ...core.diagnostics import UnitDiagnostics
from ...core.provider_mapping import (
    TEMPLATE_VARIABLES,
    ProviderMappingError,
    render_template,
    select_all,
    select_first,
)
from ...core.redaction import redact_sensitive_text
from ...core.utils import utc_now
from ...schemas.generation import GenerateRequest
from ...schemas.provider import (
    EDIT_TEMPLATE_VARIABLES,
    ResolvedProviderConfig,
    resolve_provider_config,
)
from ..session_pool import TIMEOUT_UPSTREAM, get_pool
from .contracts import EditUploads
from .errors import UpstreamApiError, _warn_if_socks5_upstream_resolves_private
from .payloads import sent_generation_prompt
from .transport import (
    ProgressCallback,
    parse_upstream_json_response,
    read_limited_text_response,
)

CheckpointCallback = Callable[[dict[str, Any]], Awaitable[None]]
ShouldCancelRemote = Callable[[], Awaitable[bool]]

_MODEL_IN_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,199}$")
_MODEL_PLACEHOLDER = re.compile(r"\{\{\s*model\s*\}\}")
_TASK_ID_PLACEHOLDER = re.compile(r"\{\{\s*task_id\s*\}\}")
_CANCEL_TIMEOUT_SECONDS = 10.0
_MAX_POLL_DELAY_SECONDS = 15.0
_MAX_TASK_ID_CHARS = 512
_MAX_POLL_ERROR_TEXT_CHARS = 500
_MAX_RETRYABLE_RESPONSE_BYTES = 64 * 1024
_RETRYABLE_POLL_STATUSES = frozenset({408, 425, 429})
_PROVIDER_LABEL = "async provider"


class _PollTimeout(Exception):
    pass


class _RetryablePollError(Exception):
    """Transient poll failure: bounded backoff may retry it until the deadline."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class _PollHttpError(UpstreamApiError):
    """Terminal poll HTTP failure (4xx other than throttling)."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_provider_config(raw: Any) -> ResolvedProviderConfig:
    try:
        return resolve_provider_config(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise UpstreamApiError(
            f"Invalid provider_config ({location}): {first.get('msg', 'invalid value')}"
        ) from exc
    except ValueError as exc:
        raise UpstreamApiError(
            redact_sensitive_text(f"Invalid provider_config: {exc}")
        ) from exc


def _size_variables(payload: GenerateRequest) -> dict[str, Any]:
    if payload.size == "auto":
        return {}
    width_text, height_text = payload.size.lower().split("x", 1)
    return {"size": payload.size, "width": int(width_text), "height": int(height_text)}


def build_template_variables(payload: GenerateRequest, *, prompt_guard: bool) -> dict[str, Any]:
    # Chroma backgrounds request an opaque canvas upstream (the local keying
    # pass removes it afterwards), mirroring the OpenAI payload behavior.
    background = "opaque" if payload.background.startswith("chroma_") else payload.background
    return {
        "prompt": sent_generation_prompt(payload, prompt_guard=prompt_guard),
        "model": payload.model,
        "n": payload.n,
        "quality": payload.quality,
        "output_format": payload.output_format,
        "background": background,
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


def render_url_template(template: str, task_id: str) -> str:
    """Render a follow-up path template, encoding the task id as a path segment."""
    if not task_id:
        raise UpstreamApiError("Provider task id is empty; cannot build a follow-up URL")
    if len(task_id) > _MAX_TASK_ID_CHARS:
        raise UpstreamApiError("Provider task id is too long to use in a follow-up URL")
    return _TASK_ID_PLACEHOLDER.sub(quote(task_id, safe=""), template)


def append_query(
    url: str,
    query_pairs: tuple[tuple[str, str], ...],
    variables: dict[str, Any],
    *,
    allowed: frozenset[str],
) -> str:
    """Render mapped query parameters onto a URL; omitted values drop the param.

    ``urlencode`` percent-encodes each rendered value exactly once, so task ids
    and model names are safe to embed without pre-quoting.
    """
    if not query_pairs:
        return url
    pairs: list[tuple[str, str]] = []
    for key, template in query_pairs:
        value = render_template(template, variables, allowed=allowed)
        if value is None or (isinstance(value, dict) and not value):
            continue
        pairs.append((key, str(value)))
    if not pairs:
        return url
    return url + ("&" if "?" in url else "?") + urlencode(pairs)


def _auth_headers(
    cfg: ResolvedProviderConfig,
    api_key: str,
    *,
    idempotency_key: str = "",
    json_body: bool = True,
) -> dict[str, str]:
    value = f"{cfg.auth.scheme} {api_key}".strip()
    headers = {
        cfg.auth.header: value,
        "User-Agent": "opencode",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    if idempotency_key and cfg.submit.idempotency_header:
        headers[cfg.submit.idempotency_header] = idempotency_key
    return headers


async def _validated_upstream_url(url: str) -> str:
    try:
        await ssrf.validate_upstream_url_async(url, config.UPSTREAM_HOST_ALLOWLIST, allow_query=True)
    except ValueError as exc:
        raise UpstreamApiError(redact_sensitive_text(f"Provider URL rejected: {exc}")) from exc
    return url


async def _validated_followup_url(value: str, api_url: str, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise UpstreamApiError(f"Provider {label} URL is empty")
    if not secrets.same_origin(api_url, value):
        # The API key travels with these requests, so never follow another origin.
        raise UpstreamApiError(
            f"Provider {label} URL must be on the same origin as the preset API URL"
        )
    return await _validated_upstream_url(value)


def _select_string(source: Any, path: str | None) -> str | None:
    if source is None or not path:
        return None
    value = select_first(source, path)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _select_task_id(source: Any, path: str | None) -> str | None:
    return _select_string(source, path)


async def _request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    socks5_proxy: str | None,
    json_body: Any = None,
) -> tuple[dict[str, Any], int]:
    request = getattr(session, method.lower())
    kwargs: dict[str, Any] = {"headers": headers, "allow_redirects": False}
    if json_body is not None:
        kwargs["json"] = json_body
    async with request(url, **kwargs) as resp:
        status = int(resp.status)
        if not socks5_proxy:
            ssrf.validate_response_peer_ip(resp, "Upstream API")
        result, _preview = await parse_upstream_json_response(resp, _PROVIDER_LABEL, None)
    return result, status


def _form_field_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


async def _submit_provider_request(
    session: aiohttp.ClientSession,
    submit: Any,
    url: str,
    *,
    headers: dict[str, str],
    socks5_proxy: str | None,
    body: Any,
    uploads: EditUploads | None,
) -> tuple[dict[str, Any], int]:
    """Send one submit request as JSON or multipart and parse the JSON reply."""
    if submit.body_format == "multipart":
        mask_field = str(getattr(submit, "files_mask", "") or "")
        # Own file handles even when connection setup, form construction or
        # cancellation happens before aiohttp consumes the upload payload.
        with ExitStack() as files:
            form = aiohttp.FormData()
            for key, value in body.items():
                form.add_field(key, _form_field_value(value))
            if uploads is not None:
                for part in uploads.parts:
                    form.add_field(
                        submit.files_images,
                        files.enter_context(part.temp_path.open("rb")),
                        filename=part.filename,
                        content_type=part.content_type or "application/octet-stream",
                    )
                if mask_field and uploads.mask_part is not None:
                    form.add_field(
                        mask_field,
                        files.enter_context(uploads.mask_part.temp_path.open("rb")),
                        filename=uploads.mask_part.filename,
                        content_type=uploads.mask_part.content_type or "image/png",
                    )
            async with session.post(
                url, data=form, headers=headers, allow_redirects=False
            ) as resp:
                status = int(resp.status)
                if not socks5_proxy:
                    ssrf.validate_response_peer_ip(resp, "Upstream API")
                result, _preview = await parse_upstream_json_response(resp, _PROVIDER_LABEL, None)
        return result, status
    return await _request_json(
        session,
        submit.method,
        url,
        headers=headers,
        socks5_proxy=socks5_proxy,
        json_body=body or None,
    )


async def _poll_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str],
    socks5_proxy: str | None,
    method: str = "GET",
) -> tuple[dict[str, Any], int]:
    """Fetch one poll response.

    Throttling and server errors are raised as retryable; other 4xx errors are
    terminal and keep their HTTP status for diagnostics.
    """
    request = getattr(session, method.lower())
    kwargs: dict[str, Any] = {"headers": headers, "allow_redirects": False}
    if method == "POST":
        kwargs["json"] = {}
    async with request(url, **kwargs) as resp:
        status = int(resp.status)
        if not socks5_proxy:
            ssrf.validate_response_peer_ip(resp, "Upstream API")
        if status in _RETRYABLE_POLL_STATUSES or status >= 500:
            text = await read_limited_text_response(
                resp, _MAX_RETRYABLE_RESPONSE_BYTES, label="Provider poll response"
            )
            raise _RetryablePollError(
                redact_sensitive_text(f"HTTP {status}: {text[:_MAX_POLL_ERROR_TEXT_CHARS]}"),
                status=status,
            )
        try:
            result, _preview = await parse_upstream_json_response(resp, _PROVIDER_LABEL, None)
        except UpstreamApiError as exc:
            raise _PollHttpError(str(exc), status=status) from exc
    return result, status


async def _cancel_remote(
    session: aiohttp.ClientSession,
    cfg: ResolvedProviderConfig,
    *,
    cancel_url: str | None,
    headers: dict[str, str],
    socks5_proxy: str | None,
) -> None:
    """Best effort: a failed cancel must never mask the original error."""
    if cfg.cancel is None or not cancel_url:
        return
    try:
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


async def _resolve_status_url(
    cfg: ResolvedProviderConfig,
    submit_result: dict[str, Any],
    *,
    task_id: str | None,
    api_url: str,
) -> tuple[str, str | None]:
    assert cfg.poll is not None
    poll_query = cfg.poll.query
    if cfg.poll.url_path:
        value = _select_string(submit_result, cfg.poll.url_path)
        if not value:
            raise UpstreamApiError(
                f"Provider response did not include a status URL at {cfg.poll.url_path}"
            )
        url = await _validated_followup_url(value, api_url, "status")
        url = append_query(url, poll_query, _task_query_vars(task_id), allowed={"task_id"})
        return url, task_id
    resolved_task_id = task_id or _select_task_id(submit_result, cfg.poll.task_id_path)
    if not resolved_task_id:
        raise UpstreamApiError(
            f"Provider response did not include a task id at {cfg.poll.task_id_path}"
        )
    url = api_url.rstrip("/") + render_url_template(str(cfg.poll.url_template), resolved_task_id)
    url = append_query(url, poll_query, {"task_id": resolved_task_id}, allowed={"task_id"})
    url = await _validated_upstream_url(url)
    return url, resolved_task_id


def _task_query_vars(task_id: str | None) -> dict[str, Any]:
    return {"task_id": task_id} if task_id else {}


async def _resolve_submit_result_url(
    cfg: ResolvedProviderConfig,
    *,
    submit_result: dict[str, Any],
    task_id: str | None,
    api_url: str,
    diag: UnitDiagnostics | None,
) -> str | None:
    source = cfg.result
    try:
        if source.url_path:
            value = _select_string(submit_result, source.url_path)
            if not value:
                return None
            return await _validated_followup_url(value, api_url, "result")
        if source.task_id_path and source.url_template:
            resolved = task_id or _select_task_id(submit_result, source.task_id_path)
            if not resolved:
                return None
            return await _validated_upstream_url(
                api_url.rstrip("/") + render_url_template(source.url_template, resolved)
            )
    except UpstreamApiError as exc:
        if diag:
            diag.record_event(
                "submit",
                "Provider result URL was rejected",
                mapping_path=source.url_path or source.task_id_path,
                error=str(exc),
            )
        raise
    return None


async def _resolve_cancel_url(
    cfg: ResolvedProviderConfig,
    *,
    submit_result: dict[str, Any],
    task_id: str | None,
    api_url: str,
    diag: UnitDiagnostics | None,
) -> str | None:
    cancel = cfg.cancel
    if cancel is None:
        return None
    try:
        if cancel.url_path:
            value = _select_string(submit_result, cancel.url_path)
            if not value:
                return None
            return await _validated_followup_url(value, api_url, "cancel")
        resolved = task_id or _select_task_id(submit_result, cancel.task_id_path)
        if not resolved:
            return None
        return await _validated_upstream_url(
            api_url.rstrip("/") + render_url_template(str(cancel.url_template), resolved)
        )
    except UpstreamApiError as exc:
        if diag:
            diag.record_event(
                "submit",
                "Provider cancel URL was rejected; cancel will be skipped",
                mapping_path=cancel.url_path or cancel.task_id_path,
                error=str(exc),
            )
        return None


async def _resolve_result_url(
    cfg: ResolvedProviderConfig,
    *,
    submit_result: dict[str, Any] | None,
    poll_result: dict[str, Any],
    task_id: str | None,
    api_url: str,
    diag: UnitDiagnostics | None,
) -> str | None:
    source = cfg.result
    try:
        if source.url_path:
            for holder in (submit_result, poll_result):
                value = _select_string(holder, source.url_path)
                if value:
                    return await _validated_followup_url(value, api_url, "result")
            raise UpstreamApiError(
                f"Provider response did not include a result URL at {source.url_path}"
            )
        if source.task_id_path and source.url_template:
            resolved = (
                task_id
                or _select_task_id(submit_result, source.task_id_path)
                or _select_task_id(poll_result, source.task_id_path)
            )
            if not resolved:
                raise UpstreamApiError(
                    f"Provider response did not include a task id at {source.task_id_path}"
                )
            return await _validated_upstream_url(
                api_url.rstrip("/") + render_url_template(source.url_template, resolved)
            )
    except UpstreamApiError as exc:
        if diag:
            diag.set_code("result_url_missing")
            diag.record_event(
                "result",
                str(exc),
                mapping_path=source.url_path or source.task_id_path,
                error=str(exc),
            )
        raise
    return None


def _image_items(source: Any, cfg: ResolvedProviderConfig) -> list[dict[str, Any]]:
    key = "url" if cfg.result.image_kind == "url" else "b64_json"
    items = []
    for value in select_all(source, cfg.result.images_path):
        if isinstance(value, str) and value.strip():
            items.append({key: value.strip()})
    return items


def _parse_deadline(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def run_async_provider(
    *,
    api_url: str,
    api_key: str,
    provider_config: Any,
    payload: GenerateRequest,
    progress: ProgressCallback | None,
    socks5_proxy: str | None,
    prompt_guard: bool = False,
    remote: dict[str, Any] | None = None,
    checkpoint: CheckpointCallback | None = None,
    should_cancel_remote: ShouldCancelRemote | None = None,
    diagnostics: UnitDiagnostics | None = None,
    edit: EditUploads | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Run one task. Returns the image entries and a short response preview.

    `remote` carries a persisted checkpoint: phase "submitted" resumes polling
    without resubmitting; phase "submitting" retries the submit under the same
    idempotency key when the mapping declares one. A fresh unit passes
    `remote=None`. `edit` carries the validated reference images and mask for
    an edit task; the mapping must then declare an `edit_submit` section.
    """
    cfg = load_provider_config(provider_config)
    diag = diagnostics
    if diag is not None:
        diag.add_secrets(api_key, socks5_proxy)
    is_edit = edit is not None
    if is_edit and cfg.edit_submit is None:
        raise UpstreamApiError(
            "Provider mapping does not declare image edit support; "
            "add an edit_submit section to provider_config"
        )
    submit_cfg = cfg.edit_submit if is_edit else cfg.submit
    assert submit_cfg is not None
    sync_mode = cfg.mode == "sync"
    session = get_pool().get(timeout_kind=TIMEOUT_UPSTREAM, socks5_proxy=socks5_proxy)
    state_api_url = str(api_url).rstrip("/")
    remote_state = dict(remote or {})
    phase = str(remote_state.get("phase") or "")
    if remote_state.get("api_url") and not secrets.same_origin(api_url, remote_state["api_url"]):
        raise UpstreamApiError(
            "Provider checkpoint origin differs from the current preset; start a new task"
        )
    if phase == "submitted":
        state_api_url = str(remote_state.get("api_url") or state_api_url).rstrip("/")
    headers = _auth_headers(cfg, api_key)

    submit_result: dict[str, Any] | None = None
    status_url = ""
    task_id: str | None = None
    result_url: str | None = None
    cancel_url: str | None = None

    if phase == "submitted":
        task_id = str(remote_state.get("task_id") or "") or None
        status_url = await _validated_followup_url(
            str(remote_state.get("status_url") or ""), state_api_url, "status"
        )
        if remote_state.get("result_url"):
            result_url = await _validated_followup_url(
                str(remote_state["result_url"]), state_api_url, "result"
            )
        if remote_state.get("cancel_url") and cfg.cancel is not None:
            try:
                cancel_url = await _validated_followup_url(
                    str(remote_state["cancel_url"]), state_api_url, "cancel"
                )
            except UpstreamApiError:
                cancel_url = None
        deadline = _parse_deadline(remote_state.get("deadline_at"))
        if deadline is None:
            assert cfg.poll is not None
            deadline = _now() + timedelta(seconds=int(cfg.poll.timeout_seconds))
        if progress:
            progress("polling_provider", "Resuming provider task recovery")
    else:
        idempotency_key = str(remote_state.get("idempotency_key") or "")
        if phase == "submitting":
            if not (cfg.submit.idempotency_header and idempotency_key):
                raise UpstreamApiError(
                    "Provider submit result is unknown and the mapping has no idempotency "
                    "key for this task; automatic resubmission is not attempted"
                )
        else:
            idempotency_key = uuid.uuid4().hex if cfg.submit.idempotency_header else ""
            remote_state = {
                "phase": "submitting",
                "mode": cfg.mode,
                "operation": "edit" if is_edit else "generation",
                "api_url": state_api_url,
                "origin": secrets.canonical_origin(state_api_url),
                "provider_config": cfg.snapshot,
                "submitted_at": utc_now(),
            }
            if idempotency_key:
                remote_state["idempotency_key"] = idempotency_key
        if checkpoint is not None:
            await checkpoint(dict(remote_state))

        variables = build_template_variables(payload, prompt_guard=prompt_guard)
        allowed = EDIT_TEMPLATE_VARIABLES if is_edit else TEMPLATE_VARIABLES
        if is_edit:
            assert edit is not None
            variables.update(edit.inline_variables)
        try:
            body = render_template(submit_cfg.body, variables, allowed=allowed)
        except ProviderMappingError as exc:
            if diag:
                diag.set_code("request_template_invalid")
                diag.record_event("submit", "Cannot build provider request", error=str(exc))
            raise UpstreamApiError(f"Cannot build provider request: {exc}") from exc

        submit_path = render_submit_path(
            submit_cfg.path or cfg.submit.path, payload.model
        )
        submit_url = state_api_url + submit_path
        submit_url = append_query(submit_url, submit_cfg.query, variables, allowed=allowed)
        submit_url = await _validated_upstream_url(submit_url)
        await _warn_if_socks5_upstream_resolves_private(submit_url, socks5_proxy)

        if progress:
            progress("submitting_provider_task", "Submitting task to provider")
        request_headers = _auth_headers(
            cfg,
            api_key,
            idempotency_key=idempotency_key,
            json_body=submit_cfg.body_format == "json",
        )
        try:
            submit_result, submit_status = await _submit_provider_request(
                session,
                submit_cfg,
                submit_url,
                headers=request_headers,
                socks5_proxy=socks5_proxy,
                body=body,
                uploads=edit,
            )
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            if diag:
                diag.set_code("submit_unknown")
                diag.record_event(
                    "submit",
                    "Provider submit request failed without a response; "
                    "the task is not resubmitted automatically",
                    error=str(exc),
                )
            raise UpstreamApiError(
                "Provider submit request failed without a response; no automatic "
                "resubmission was attempted"
            ) from exc
        if diag:
            diag.record_http(
                "submit",
                method="POST" if submit_cfg.body_format == "multipart" else submit_cfg.method,
                url=submit_url,
                status=submit_status,
                snapshot=submit_result,
            )

        if sync_mode:
            # The submit response carries the final images; there is no remote
            # task to resume, so the "submitting" checkpoint stays the only
            # recovery state (same-key retry or interrupted, per Phase 1).
            items = _image_items(submit_result, cfg)
            if not items:
                if diag:
                    diag.set_code("result_extraction_failed")
                    diag.record_http(
                        "result",
                        method=submit_cfg.method,
                        url=submit_url,
                        snapshot=submit_result,
                        mapping_path=cfg.result.images_path,
                    )
                raise UpstreamApiError(
                    f"Provider result did not include any images at {cfg.result.images_path}"
                )
            return items, f"{_PROVIDER_LABEL} result: {len(items)} image(s)"

        assert cfg.poll is not None
        task_id = _select_task_id(submit_result, cfg.poll.task_id_path)
        if cfg.poll.task_id_path and not task_id:
            if diag:
                diag.set_code("task_id_missing")
                diag.record_event(
                    "submit",
                    f"Provider response did not include a task id at {cfg.poll.task_id_path}",
                    mapping_path=cfg.poll.task_id_path,
                    error="task id missing",
                )
            raise UpstreamApiError(
                f"Provider response did not include a task id at {cfg.poll.task_id_path}"
            )
        try:
            status_url, task_id = await _resolve_status_url(
                cfg, submit_result, task_id=task_id, api_url=state_api_url
            )
        except UpstreamApiError as exc:
            if diag:
                diag.set_code(
                    "task_id_missing" if cfg.poll.task_id_path else "status_url_missing"
                )
                diag.record_event(
                    "submit",
                    str(exc),
                    mapping_path=cfg.poll.url_path or cfg.poll.task_id_path,
                    error=str(exc),
                )
            raise
        result_url = await _resolve_submit_result_url(
            cfg,
            submit_result=submit_result,
            task_id=task_id,
            api_url=state_api_url,
            diag=diag,
        )
        cancel_url = await _resolve_cancel_url(
            cfg,
            submit_result=submit_result,
            task_id=task_id,
            api_url=state_api_url,
            diag=diag,
        )
        deadline = _now() + timedelta(
            seconds=int(cfg.poll.timeout_seconds)
        )
        remote_state = {
            **remote_state,
            "phase": "submitted",
            "api_url": state_api_url,
            "origin": secrets.canonical_origin(state_api_url),
            "provider_config": cfg.snapshot,
            "deadline_at": deadline.isoformat(),
            "task_id": task_id,
            "status_url": status_url,
            "result_url": result_url,
            "cancel_url": cancel_url,
        }
        if checkpoint is not None:
            await checkpoint(dict(remote_state))

    assert cfg.poll is not None
    poll_method = str(getattr(cfg.poll, "method", "GET") or "GET")
    delay = float(cfg.poll.interval_seconds)
    poll_result: dict[str, Any] = {}
    poll_count = 0
    last_status: str | None = None
    try:
        while True:
            remaining = (deadline - _now()).total_seconds()
            if remaining <= 0:
                raise _PollTimeout()
            try:
                poll_result, poll_status = await asyncio.wait_for(
                    _poll_json(
                        session,
                        status_url,
                        headers=headers,
                        socks5_proxy=socks5_proxy,
                        method=poll_method,
                    ),
                    timeout=remaining,
                )
            except _RetryablePollError as exc:
                if diag:
                    diag.record_http(
                        "poll",
                        method=poll_method,
                        url=status_url,
                        status=exc.status,
                        error=str(exc),
                        extra={"retryable": True},
                    )
                if _now() >= deadline:
                    raise _PollTimeout() from None
                if progress:
                    progress("polling_provider", "Provider poll request failed; retrying")
                await _sleep(min(delay, max(0.0, (deadline - _now()).total_seconds())))
                delay = min(
                    delay * 1.25, max(float(cfg.poll.interval_seconds), _MAX_POLL_DELAY_SECONDS)
                )
                continue
            except _PollHttpError as exc:
                if diag:
                    diag.record_http(
                        "poll",
                        method=poll_method,
                        url=status_url,
                        status=exc.status,
                        error=str(exc),
                    )
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                if diag:
                    diag.record_event("poll", "Provider poll request failed", error=str(exc))
                if _now() >= deadline:
                    raise _PollTimeout() from None
                if progress:
                    progress("polling_provider", "Provider poll request failed; retrying")
                await _sleep(min(delay, max(0.0, (deadline - _now()).total_seconds())))
                delay = min(
                    delay * 1.25, max(float(cfg.poll.interval_seconds), _MAX_POLL_DELAY_SECONDS)
                )
                continue

            poll_count += 1
            raw_status = select_first(poll_result, cfg.poll.status_path)
            status = "" if raw_status is None else str(raw_status)
            if diag and (poll_count == 1 or status != last_status):
                diag.record_http(
                    "poll",
                    method=poll_method,
                    url=status_url,
                    status=poll_status,
                    snapshot=poll_result,
                    extra={"status": status, "poll_count": poll_count},
                )
            last_status = status
            if status in cfg.poll.done:
                break
            if status in cfg.poll.failed:
                if diag:
                    diag.set_code("provider_task_failed")
                raise UpstreamApiError(
                    redact_sensitive_text(f"Provider task failed with status {status}")
                )
            if _now() >= deadline:
                raise _PollTimeout()
            if progress:
                progress("polling_provider", f"Provider task status: {status or 'unknown'}")
            await _sleep(min(delay, max(0.0, (deadline - _now()).total_seconds())))
            delay = min(
                delay * 1.25, max(float(cfg.poll.interval_seconds), _MAX_POLL_DELAY_SECONDS)
            )
    except _PollTimeout:
        if diag:
            diag.set_code("poll_timeout")
        await _cancel_remote(
            session, cfg, cancel_url=cancel_url, headers=headers, socks5_proxy=socks5_proxy
        )
        raise UpstreamApiError(
            f"Provider task did not finish within {cfg.poll.timeout_seconds} seconds"
        ) from None
    except asyncio.CancelledError:
        cancel_allowed = True
        if should_cancel_remote is not None:
            try:
                cancel_allowed = bool(await should_cancel_remote())
            except Exception:  # noqa: BLE001 - never mask the cancellation
                cancel_allowed = False
        if cancel_allowed:
            await _cancel_remote(
                session, cfg, cancel_url=cancel_url, headers=headers, socks5_proxy=socks5_proxy
            )
        raise

    source: Any = poll_result
    if result_url is None:
        result_url = await _resolve_result_url(
            cfg,
            submit_result=submit_result,
            poll_result=poll_result,
            task_id=task_id,
            api_url=state_api_url,
            diag=diag,
        )
    if result_url:
        if progress:
            progress("fetching_provider_result", "Fetching provider result")
        source, _status = await _request_json(
            session, "GET", result_url, headers=headers, socks5_proxy=socks5_proxy
        )

    items = _image_items(source, cfg)
    if not items:
        if diag:
            diag.set_code("result_extraction_failed")
            diag.record_http(
                "result",
                method="GET",
                url=result_url or status_url,
                snapshot=source,
                mapping_path=cfg.result.images_path,
            )
        raise UpstreamApiError(
            f"Provider result did not include any images at {cfg.result.images_path}"
        )
    return items, f"{_PROVIDER_LABEL} result: {len(items)} image(s)"
