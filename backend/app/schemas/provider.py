import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    BeforeValidator,
    Field,
    field_validator,
    model_validator,
)

from ..core.provider_mapping import (
    TEMPLATE_VARIABLES,
    ProviderMappingError,
    parse_json_path,
    template_variables,
    validate_template_shape,
)
from .common import StrictRequestModel

MAX_PROVIDER_CONFIG_BYTES = 16 * 1024
_MODEL_PLACEHOLDER = re.compile(r"\{\{\s*model\s*\}\}")
_TASK_ID_PLACEHOLDER = re.compile(r"\{\{\s*task_id\s*\}\}")
_ANY_PLACEHOLDER = re.compile(r"\{\{")
_TOKEN = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")
_PLAIN_PATH = re.compile(r"^(/[A-Za-z0-9._~\-/]*)?$")


def _check_path(path: str, label: str) -> str:
    try:
        parse_json_path(path)
    except ProviderMappingError as exc:
        raise ValueError(f"{label}: {exc}") from exc
    return path.strip()


def _check_url_template(template: str, label: str) -> str:
    value = template.strip()
    if not _TASK_ID_PLACEHOLDER.search(value):
        raise ValueError(f"{label} must contain {{{{task_id}}}}")
    plain = _TASK_ID_PLACEHOLDER.sub("t", value)
    if (
        not plain.startswith("/")
        or len(plain) < 2
        or ".." in plain
        or "//" in plain
        or not _PLAIN_PATH.match(plain)
        or _ANY_PLACEHOLDER.search(plain)
    ):
        raise ValueError(
            f"{label} must be a plain path starting with '/' (letters, digits, . _ ~ - /) "
            "and may only contain {{task_id}}"
        )
    return value


def _check_config_size(model: StrictRequestModel, label: str) -> None:
    if len(json.dumps(model.model_dump(mode="json")).encode("utf-8")) > MAX_PROVIDER_CONFIG_BYTES:
        raise ValueError(f"{label} is too large")


class ProviderAuth(StrictRequestModel):
    # The key itself always comes from the preset's api_key; only its wire shape is configurable.
    header: Literal["Authorization", "X-API-Key"] = "Authorization"
    scheme: Literal["Bearer", "Key", "Token", ""] = "Bearer"


class ProviderSubmit(StrictRequestModel):
    path: str = Field(default="", max_length=512, description="Path appended to the preset API URL; may use {{model}}.")
    body: dict[str, Any]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = value.strip()
        plain = _MODEL_PLACEHOLDER.sub("m", value)
        if ".." in plain or "//" in plain or not _PLAIN_PATH.match(plain):
            raise ValueError(
                "submit.path must be a plain path starting with '/' (letters, digits, . _ ~ - /) "
                "that may contain {{model}}"
            )
        return value

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_template_shape(value)
        except ProviderMappingError as exc:
            raise ValueError(f"submit.body: {exc}") from exc
        return value

    @model_validator(mode="after")
    def validate_placeholders(self) -> "ProviderSubmit":
        unknown = template_variables(self.body) - TEMPLATE_VARIABLES
        if unknown:
            raise ValueError(f"unknown template variables: {', '.join(sorted(unknown))}")
        if template_variables(self.path) - {"model"}:
            raise ValueError("submit.path may only use the {{model}} variable")
        return self


class ProviderPoll(StrictRequestModel):
    url_path: str = Field(max_length=200, description="Path into the submit response that holds the status URL.")
    status_path: str = Field(max_length=200)
    done: list[str] = Field(min_length=1, max_length=16)
    failed: list[str] = Field(default_factory=list, max_length=16)
    interval_seconds: float = Field(default=2.0, ge=0.5, le=30)
    timeout_seconds: int = Field(default=600, ge=10, le=3600)

    @field_validator("url_path")
    @classmethod
    def validate_url_path(cls, value: str) -> str:
        return _check_path(value, "poll.url_path")

    @field_validator("status_path")
    @classmethod
    def validate_status_path(cls, value: str) -> str:
        return _check_path(value, "poll.status_path")


class ProviderResult(StrictRequestModel):
    url_path: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional path holding a result URL to fetch once the task is done.",
    )
    images_path: str = Field(max_length=200)
    image_kind: Literal["url", "b64_json"] = "url"

    @field_validator("url_path")
    @classmethod
    def validate_url_path(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_path(value, "result.url_path")

    @field_validator("images_path")
    @classmethod
    def validate_images_path(cls, value: str) -> str:
        return _check_path(value, "result.images_path")


class ProviderCancel(StrictRequestModel):
    url_path: str = Field(max_length=200)
    method: Literal["PUT", "POST", "DELETE"] = "PUT"

    @field_validator("url_path")
    @classmethod
    def validate_url_path(cls, value: str) -> str:
        return _check_path(value, "cancel.url_path")


class ProviderConfig(StrictRequestModel):
    """Version 1 mapping: follow-up URLs are read from upstream responses."""

    version: Literal[1] = 1
    auth: ProviderAuth = Field(default_factory=ProviderAuth)
    submit: ProviderSubmit
    poll: ProviderPoll
    result: ProviderResult
    cancel: Optional[ProviderCancel] = None

    @model_validator(mode="after")
    def validate_size(self) -> "ProviderConfig":
        _check_config_size(self, "provider_config")
        return self


class ProviderSubmitV2(ProviderSubmit):
    idempotency_header: str = Field(
        default="",
        max_length=128,
        description="Optional request header used to send a stable per-unit idempotency key.",
    )

    @field_validator("idempotency_header")
    @classmethod
    def validate_idempotency_header(cls, value: str) -> str:
        value = value.strip()
        if value and not _TOKEN.match(value):
            raise ValueError("submit.idempotency_header must be a valid HTTP header name")
        return value


class ProviderPollV2(StrictRequestModel):
    """Version 2 status source: a direct URL path, or a task id plus URL template."""

    url_path: Optional[str] = Field(default=None, max_length=200)
    task_id_path: Optional[str] = Field(default=None, max_length=200)
    url_template: Optional[str] = Field(default=None, max_length=512)
    status_path: str = Field(max_length=200)
    done: list[str] = Field(min_length=1, max_length=16)
    failed: list[str] = Field(default_factory=list, max_length=16)
    interval_seconds: float = Field(default=2.0, ge=0.5, le=30)
    timeout_seconds: int = Field(default=600, ge=10, le=3600)

    @field_validator("url_path")
    @classmethod
    def validate_url_path(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_path(value, "poll.url_path")

    @field_validator("task_id_path")
    @classmethod
    def validate_task_id_path(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_path(value, "poll.task_id_path")

    @field_validator("url_template")
    @classmethod
    def validate_template(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_url_template(value, "poll.url_template")

    @field_validator("status_path")
    @classmethod
    def validate_status_path(cls, value: str) -> str:
        return _check_path(value, "poll.status_path")

    @model_validator(mode="after")
    def validate_source(self) -> "ProviderPollV2":
        if self.url_path is not None and (
            self.task_id_path is not None or self.url_template is not None
        ):
            raise ValueError(
                "poll may use either url_path or task_id_path with url_template, not both"
            )
        if self.url_path is None and self.task_id_path is None and self.url_template is None:
            raise ValueError("poll needs url_path or task_id_path with url_template")
        if self.task_id_path is not None and self.url_template is None:
            raise ValueError("poll.url_template is required when poll.task_id_path is set")
        if self.url_template is not None and self.task_id_path is None:
            raise ValueError("poll.task_id_path is required when poll.url_template is set")
        return self


class ProviderResultV2(StrictRequestModel):
    url_path: Optional[str] = Field(default=None, max_length=200)
    task_id_path: Optional[str] = Field(default=None, max_length=200)
    url_template: Optional[str] = Field(default=None, max_length=512)
    images_path: str = Field(max_length=200)
    image_kind: Literal["url", "b64_json"] = "url"

    @field_validator("url_path")
    @classmethod
    def validate_url_path(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_path(value, "result.url_path")

    @field_validator("task_id_path")
    @classmethod
    def validate_task_id_path(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_path(value, "result.task_id_path")

    @field_validator("url_template")
    @classmethod
    def validate_template(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_url_template(value, "result.url_template")

    @field_validator("images_path")
    @classmethod
    def validate_images_path(cls, value: str) -> str:
        return _check_path(value, "result.images_path")

    @model_validator(mode="after")
    def validate_source(self) -> "ProviderResultV2":
        if self.url_path is not None and (
            self.task_id_path is not None or self.url_template is not None
        ):
            raise ValueError(
                "result may use either url_path or task_id_path with url_template, not both"
            )
        if self.task_id_path is not None and self.url_template is None:
            raise ValueError("result.url_template is required when result.task_id_path is set")
        if self.url_template is not None and self.task_id_path is None:
            raise ValueError("result.task_id_path is required when result.url_template is set")
        return self


class ProviderCancelV2(StrictRequestModel):
    url_path: Optional[str] = Field(default=None, max_length=200)
    task_id_path: Optional[str] = Field(default=None, max_length=200)
    url_template: Optional[str] = Field(default=None, max_length=512)
    method: Literal["PUT", "POST", "DELETE"] = "PUT"

    @field_validator("url_path")
    @classmethod
    def validate_url_path(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_path(value, "cancel.url_path")

    @field_validator("task_id_path")
    @classmethod
    def validate_task_id_path(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_path(value, "cancel.task_id_path")

    @field_validator("url_template")
    @classmethod
    def validate_template(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _check_url_template(value, "cancel.url_template")

    @model_validator(mode="after")
    def validate_source(self) -> "ProviderCancelV2":
        has_direct = self.url_path is not None
        has_template = self.task_id_path is not None or self.url_template is not None
        if has_direct == has_template:
            raise ValueError(
                "cancel needs either url_path or task_id_path with url_template"
            )
        if self.task_id_path is not None and self.url_template is None:
            raise ValueError("cancel.url_template is required when cancel.task_id_path is set")
        if self.url_template is not None and self.task_id_path is None:
            raise ValueError("cancel.task_id_path is required when cancel.url_template is set")
        return self


class ProviderConfigV2(StrictRequestModel):
    """Version 2 mapping: adds task-id extraction with query/result/cancel URL templates."""

    version: Literal[2]
    auth: ProviderAuth = Field(default_factory=ProviderAuth)
    submit: ProviderSubmitV2
    poll: ProviderPollV2
    result: ProviderResultV2
    cancel: Optional[ProviderCancelV2] = None

    @model_validator(mode="after")
    def validate_size(self) -> "ProviderConfigV2":
        _check_config_size(self, "provider_config")
        return self


def _default_provider_version(value: Any) -> Any:
    if isinstance(value, dict) and "version" not in value:
        return {**value, "version": 1}
    return value


ProviderConfigPayload = Annotated[
    Union[ProviderConfig, ProviderConfigV2],
    BeforeValidator(_default_provider_version),
]


@dataclass(frozen=True)
class ResolvedSubmit:
    path: str
    body: dict[str, Any]
    idempotency_header: str = ""


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
class ResolvedProviderConfig:
    """Version-independent runtime shape used by the async provider driver."""

    version: int
    auth: ProviderAuth
    submit: ResolvedSubmit
    poll: ResolvedPoll
    result: ResolvedResult
    cancel: Optional[ResolvedCancel]
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


def resolve_provider_config(raw: Any) -> ResolvedProviderConfig:
    """Validate and normalize any supported mapping version for execution."""
    config = parse_provider_config(raw)
    snapshot = config.model_dump(mode="json")
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
    return ResolvedProviderConfig(cancel=cancel, **common)


__all__ = [
    "MAX_PROVIDER_CONFIG_BYTES",
    "ProviderAuth",
    "ProviderCancel",
    "ProviderCancelV2",
    "ProviderConfig",
    "ProviderConfigPayload",
    "ProviderConfigV2",
    "ProviderPoll",
    "ProviderPollV2",
    "ProviderResult",
    "ProviderResultV2",
    "ProviderSubmit",
    "ProviderSubmitV2",
    "ResolvedCancel",
    "ResolvedPoll",
    "ResolvedProviderConfig",
    "ResolvedResult",
    "ResolvedSubmit",
    "parse_provider_config",
    "resolve_provider_config",
]
