"""Normalized runtime view of a stored mapping (v1 and v2 resolve to the same shape)."""

import json
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import ValidationError

from ...core.provider_mapping import template_variables
from ._common import APP_IMAGE_FORMATS
from .v1 import ProviderAuth, ProviderConfig
from .v2 import ProviderConfigPayload, ProviderConfigV2, ResolvedSubmit


@dataclass(frozen=True)
class ResolvedPoll:
    url_path: Optional[str]
    task_id_path: Optional[str]
    url_template: Optional[str]
    status_path: str
    done: tuple[str, ...]
    failed: tuple[str, ...]
    interval_seconds: float
    timeout_seconds: int
    method: str = "GET"
    query: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ResolvedResult:
    url_path: Optional[str]
    task_id_path: Optional[str]
    url_template: Optional[str]
    images_path: str
    image_kind: str


@dataclass(frozen=True)
class ResolvedCancel:
    url_path: Optional[str]
    task_id_path: Optional[str]
    url_template: Optional[str]
    method: str


@dataclass(frozen=True)
class ResolvedEditSubmit:
    path: str
    method: str
    query: tuple[tuple[str, str], ...]
    body: dict[str, Any]
    body_format: str
    files_images: str
    files_mask: str


@dataclass(frozen=True)
class ProviderCapabilities:
    """Single source of truth for what a provider mapping can do.

    Both task admission and the frontend capability display consume this shape,
    so a mapping only ever offers what it actually declares.
    """

    generate: bool = True
    edit: bool = False
    mask: bool = False
    stream: bool = False
    transparent_background: bool = False
    formats: tuple[str, ...] = APP_IMAGE_FORMATS

    def as_dict(self) -> dict[str, Any]:
        return {
            "generate": self.generate,
            "edit": self.edit,
            "mask": self.mask,
            "stream": self.stream,
            "transparent_background": self.transparent_background,
            "formats": list(self.formats),
        }


@dataclass(frozen=True)
class ResolvedProviderConfig:
    """Version-independent runtime shape used by the async provider driver."""

    version: int
    auth: ProviderAuth
    submit: ResolvedSubmit
    poll: Optional[ResolvedPoll]
    result: ResolvedResult
    cancel: Optional[ResolvedCancel]
    mode: str
    edit_submit: Optional[ResolvedEditSubmit]
    capabilities: ProviderCapabilities
    snapshot: dict[str, Any]


def parse_provider_config(raw: Any) -> ProviderConfigPayload:
    """Validate a v1 or v2 mapping, accepting either a dict or a JSON string."""
    if isinstance(raw, (ProviderConfig, ProviderConfigV2)):
        return raw
    body = raw
    if isinstance(raw, str):
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"provider_config is not valid JSON: {exc.msg}") from exc
    if not isinstance(body, dict):
        raise ValueError("provider_config must be a JSON object")
    if body.get("version") == 2:
        return ProviderConfigV2.model_validate(body)
    if "version" not in body:
        body = {**body, "version": 1}
    return ProviderConfig.model_validate(body)


def _query_pairs(value: Any) -> tuple[tuple[str, str], ...]:
    return tuple((str(key), str(val)) for key, val in (value or {}).items())


def _capabilities_from_v2(config: ProviderConfigV2) -> ProviderCapabilities:
    edit = config.edit_submit is not None
    mask = False
    if edit:
        edit_submit = config.edit_submit
        assert edit_submit is not None
        if edit_submit.body_format == "multipart":
            mask = bool(edit_submit.files.mask)
        else:
            mask = "mask" in template_variables(edit_submit.body)
    declared = config.capabilities.transparent_background
    if declared is None:
        uses_background = "background" in template_variables(config.submit.body)
        if not uses_background and edit:
            edit_submit = config.edit_submit
            assert edit_submit is not None
            uses_background = "background" in template_variables(edit_submit.body)
        transparent = uses_background
    else:
        transparent = declared
    formats = (
        tuple(config.capabilities.formats) if config.capabilities.formats else APP_IMAGE_FORMATS
    )
    return ProviderCapabilities(
        edit=edit,
        mask=mask,
        stream=bool(config.capabilities.stream),
        transparent_background=transparent,
        formats=tuple(sorted(formats, key=APP_IMAGE_FORMATS.index)),
    )


def provider_capabilities(raw: Any) -> ProviderCapabilities:
    """Best-effort capability view of any mapping shape.

    Invalid or missing configs fall back to a conservative generate-only set so
    a hand-edited row can never unlock admission paths the driver cannot run.
    """
    try:
        config = parse_provider_config(raw)
    except (ValidationError, ValueError):
        return ProviderCapabilities()
    if isinstance(config, ProviderConfig):
        # v1 mappings predate the capability contract. Their historical
        # generation behavior still applies, and transparent background stays
        # conservative: only a mapping that forwards {{background}} may
        # receive it; everything else gets a clear pre-submit reason.
        return ProviderCapabilities(
            transparent_background="background" in template_variables(config.submit.body)
        )
    return _capabilities_from_v2(config)


def resolve_provider_config(raw: Any) -> ResolvedProviderConfig:
    """Validate and normalize any supported mapping version for execution."""
    config = parse_provider_config(raw)
    snapshot = config.model_dump(mode="json")
    if isinstance(config, ProviderConfigV2):
        poll = None
        if config.poll is not None:
            poll = ResolvedPoll(
                url_path=config.poll.url_path,
                task_id_path=config.poll.task_id_path,
                url_template=config.poll.url_template,
                status_path=config.poll.status_path,
                done=tuple(config.poll.done),
                failed=tuple(config.poll.failed),
                interval_seconds=float(config.poll.interval_seconds),
                timeout_seconds=int(config.poll.timeout_seconds),
                method=str(config.poll.method),
                query=_query_pairs(config.poll.query),
            )
        edit_submit = None
        if config.edit_submit is not None:
            edit_submit = ResolvedEditSubmit(
                path=config.edit_submit.path,
                method=str(config.edit_submit.method),
                query=_query_pairs(config.edit_submit.query),
                body=config.edit_submit.body,
                body_format=str(config.edit_submit.body_format),
                files_images=config.edit_submit.files.images,
                files_mask=config.edit_submit.files.mask,
            )
        return ResolvedProviderConfig(
            version=2,
            auth=config.auth,
            submit=ResolvedSubmit(
                path=config.submit.path,
                body=config.submit.body,
                idempotency_header=config.submit.idempotency_header,
                method=str(config.submit.method),
                query=_query_pairs(config.submit.query),
                body_format=str(config.submit.body_format),
            ),
            poll=poll,
            result=ResolvedResult(
                url_path=config.result.url_path,
                task_id_path=config.result.task_id_path,
                url_template=config.result.url_template,
                images_path=config.result.images_path,
                image_kind=str(config.result.image_kind),
            ),
            cancel=(
                ResolvedCancel(
                    url_path=config.cancel.url_path,
                    task_id_path=config.cancel.task_id_path,
                    url_template=config.cancel.url_template,
                    method=str(config.cancel.method),
                )
                if config.cancel is not None
                else None
            ),
            mode=str(config.mode),
            edit_submit=edit_submit,
            capabilities=_capabilities_from_v2(config),
            snapshot=snapshot,
        )
    common = {
        "version": int(config.version),
        "auth": config.auth,
        "submit": ResolvedSubmit(
            path=config.submit.path,
            body=config.submit.body,
            idempotency_header=str(getattr(config.submit, "idempotency_header", "") or ""),
        ),
        "poll": ResolvedPoll(
            url_path=config.poll.url_path,
            task_id_path=getattr(config.poll, "task_id_path", None),
            url_template=getattr(config.poll, "url_template", None),
            status_path=config.poll.status_path,
            done=tuple(config.poll.done),
            failed=tuple(config.poll.failed),
            interval_seconds=float(config.poll.interval_seconds),
            timeout_seconds=int(config.poll.timeout_seconds),
        ),
        "result": ResolvedResult(
            url_path=config.result.url_path,
            task_id_path=getattr(config.result, "task_id_path", None),
            url_template=getattr(config.result, "url_template", None),
            images_path=config.result.images_path,
            image_kind=str(config.result.image_kind),
        ),
        "snapshot": snapshot,
    }
    cancel = None
    if config.cancel is not None:
        cancel = ResolvedCancel(
            url_path=config.cancel.url_path,
            task_id_path=getattr(config.cancel, "task_id_path", None),
            url_template=getattr(config.cancel, "url_template", None),
            method=str(config.cancel.method),
        )
    return ResolvedProviderConfig(
        cancel=cancel,
        mode="async",
        edit_submit=None,
        capabilities=provider_capabilities(config),
        **common,
    )
