import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    BeforeValidator,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from ..core.provider_mapping import (
    TEMPLATE_VARIABLES,
    _PLACEHOLDER,
    ProviderMappingError,
    parse_json_path,
    template_variables,
    validate_template_shape,
)
from .common import StrictRequestModel

MAX_PROVIDER_CONFIG_BYTES = 16 * 1024
# Variables only available when an edit task is submitted. Reference images are
# carried as a bounded list of data URLs; the mask as one data URL.
EDIT_TEMPLATE_VARIABLES = TEMPLATE_VARIABLES | {"reference_images", "mask"}
# Every image format the panel can store; mappings narrow this via capabilities.
APP_IMAGE_FORMATS: tuple[str, ...] = ("jpeg", "png", "webp")
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


QUERY_VARIABLE_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
)
_FORM_FIELD = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_MAX_QUERY_PARAMS = 16


def _check_query(value: dict[str, str], label: str, *, allowed: frozenset[str]) -> dict[str, str]:
    if len(value) > _MAX_QUERY_PARAMS:
        raise ValueError(f"{label} has too many parameters (max {_MAX_QUERY_PARAMS})")
    for key, template in value.items():
        if not isinstance(template, str):
            raise ValueError(f"{label}.{key} must be a string template")
        if not key or len(key) > 64:
            raise ValueError(f"{label}.{key} is not a valid query parameter name")
        if "{{" in key:
            raise ValueError(f"{label}.{key}: placeholders are not allowed in parameter names")
        for name in template_variables(template):
            if name not in allowed:
                raise ValueError(f"{label}.{key}: template variable '{name}' is not allowed here")
        # The rendered value must survive URL-encoding unchanged, so only
        # characters that are already safe may appear outside placeholders.
        for chunk in _PLACEHOLDER.split(template):
            if any(char not in QUERY_VARIABLE_CHARS for char in chunk):
                raise ValueError(
                    f"{label}.{key}: literal query values may only contain "
                    "letters, digits and - _ . ~"
                )
    return value


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
    method: Literal["GET", "POST"] = "POST"
    query: dict[str, str] = Field(default_factory=dict)
    body_format: Literal["json", "multipart"] = "json"

    @field_validator("idempotency_header")
    @classmethod
    def validate_idempotency_header(cls, value: str) -> str:
        value = value.strip()
        if value and not _TOKEN.match(value):
            raise ValueError("submit.idempotency_header must be a valid HTTP header name")
        return value

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_query(value, "submit.query", allowed=TEMPLATE_VARIABLES)

    @model_validator(mode="after")
    def validate_transport(self) -> "ProviderSubmitV2":
        unknown = template_variables(self.query) - TEMPLATE_VARIABLES
        if unknown:
            raise ValueError(f"submit.query unknown template variables: {', '.join(sorted(unknown))}")
        if self.method == "GET" and (self.body or self.body_format != "json"):
            raise ValueError("submit GET requests cannot carry a body or multipart fields")
        return self


class ProviderPollV2(StrictRequestModel):
    """Version 2 status source: a direct URL path, or a task id plus URL template."""

    url_path: Optional[str] = Field(default=None, max_length=200)
    task_id_path: Optional[str] = Field(default=None, max_length=200)
    url_template: Optional[str] = Field(default=None, max_length=512)
    method: Literal["GET", "POST"] = "GET"
    query: dict[str, str] = Field(default_factory=dict)
    status_path: str = Field(max_length=200)
    done: list[str] = Field(min_length=1, max_length=16)
    failed: list[str] = Field(default_factory=list, max_length=16)
    interval_seconds: float = Field(default=2.0, ge=0.5, le=30)
    timeout_seconds: int = Field(default=600, ge=10, le=3600)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_query(value, "poll.query", allowed={"task_id"})

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


class ProviderEditFilesV2(StrictRequestModel):
    """Multipart field names for edit uploads. Empty means "not carried"."""

    images: str = Field(default="", max_length=64)
    mask: str = Field(default="", max_length=64)

    @field_validator("images")
    @classmethod
    def validate_images(cls, value: str) -> str:
        value = value.strip()
        if value and not _FORM_FIELD.match(value):
            raise ValueError("edit_submit.files.images must be a form field name (letters, digits, . _ -)")
        return value

    @field_validator("mask")
    @classmethod
    def validate_mask(cls, value: str) -> str:
        value = value.strip()
        if value and not _FORM_FIELD.match(value):
            raise ValueError("edit_submit.files.mask must be a form field name (letters, digits, . _ -)")
        return value


class ProviderEditSubmitV2(StrictRequestModel):
    """How an edit task is submitted; presence declares edit capability."""

    path: str = Field(default="", max_length=512, description="Defaults to submit.path when empty; may use {{model}}.")
    method: Literal["GET", "POST"] = "POST"
    query: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    body_format: Literal["json", "multipart"] = "multipart"
    files: ProviderEditFilesV2 = Field(default_factory=ProviderEditFilesV2)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        plain = _MODEL_PLACEHOLDER.sub("m", value)
        if ".." in plain or "//" in plain or not plain.startswith("/") or not _PLAIN_PATH.match(plain):
            raise ValueError(
                "edit_submit.path must be a plain path starting with '/' that may contain {{model}}"
            )
        return value

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_query(value, "edit_submit.query", allowed=TEMPLATE_VARIABLES)

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_template_shape(value)
        except ProviderMappingError as exc:
            raise ValueError(f"edit_submit.body: {exc}") from exc
        return value

    @model_validator(mode="after")
    def validate_edit_transport(self) -> "ProviderEditSubmitV2":
        variables = template_variables(self.body) | template_variables(self.query)
        unknown = variables - EDIT_TEMPLATE_VARIABLES
        if unknown:
            raise ValueError(
                f"edit_submit unknown template variables: {', '.join(sorted(unknown))}"
            )
        if self.method == "GET" and (self.body or self.body_format != "json"):
            raise ValueError("edit_submit GET requests cannot carry a body or multipart fields")
        if self.body_format == "multipart":
            if not self.files.images:
                raise ValueError(
                    "edit_submit.files.images is required for multipart edits so "
                    "reference images are never silently dropped"
                )
            if variables & {"reference_images", "mask"}:
                raise ValueError(
                    "edit_submit.body cannot use {{reference_images}} or {{mask}} "
                    "with multipart bodies; files carry the images instead"
                )
        elif self.body_format == "json":
            if "reference_images" not in variables:
                raise ValueError(
                    "edit_submit.body must use {{reference_images}} for JSON edits "
                    "so reference images are never silently dropped"
                )
        return self


class ProviderCapabilitiesV2(StrictRequestModel):
    """Declared provider capabilities; undeclared values stay conservative.

    ``edit``/``mask`` are derived from the mapping shape and cannot be declared
    here. ``stream`` stays reserved for the Phase 4 preview work.
    """

    stream: Literal[False] = Field(
        default=False,
        description="Reserved for streamed previews; must stay false today.",
    )
    transparent_background: Optional[bool] = Field(
        default=None,
        description=(
            "Whether background=transparent is passed to the provider natively. "
            "Null auto-detects: true only when the mapping uses {{background}}."
        ),
    )
    formats: Optional[list[Literal["png", "jpeg", "webp"]]] = Field(
        default=None,
        description="Output formats this provider can produce; null allows every panel format.",
    )

    @field_validator("formats")
    @classmethod
    def validate_formats(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        if not value:
            raise ValueError("capabilities.formats must not be empty; use null for every format")
        if len(set(value)) != len(value):
            raise ValueError("capabilities.formats must not repeat a format")
        return value


class ProviderConfigV2(StrictRequestModel):
    """Version 2 mapping: task-id templates, sync mode, edit protocol, capabilities."""

    version: Literal[2]
    mode: Literal["async", "sync"] = "async"
    auth: ProviderAuth = Field(default_factory=ProviderAuth)
    submit: ProviderSubmitV2
    poll: Optional[ProviderPollV2] = None
    result: ProviderResultV2
    cancel: Optional[ProviderCancelV2] = None
    edit_submit: Optional[ProviderEditSubmitV2] = None
    capabilities: ProviderCapabilitiesV2 = Field(default_factory=ProviderCapabilitiesV2)

    @model_validator(mode="after")
    def validate_mode(self) -> "ProviderConfigV2":
        if self.mode == "async":
            if self.poll is None:
                raise ValueError("poll is required in async mode")
        else:
            if self.poll is not None:
                raise ValueError("poll must be omitted in sync mode; the submit response carries the result")
            if self.cancel is not None:
                raise ValueError("cancel is only available in async mode")
            if self.result.url_path is not None or self.result.task_id_path is not None:
                raise ValueError(
                    "sync mode reads images from the submit response; "
                    "result must not configure url_path or task_id_path"
                )
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
    method: str = "POST"
    query: tuple[tuple[str, str], ...] = ()
    body_format: str = "json"


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


__all__ = [
    "APP_IMAGE_FORMATS",
    "EDIT_TEMPLATE_VARIABLES",
    "MAX_PROVIDER_CONFIG_BYTES",
    "ProviderAuth",
    "ProviderCancel",
    "ProviderCancelV2",
    "ProviderCapabilities",
    "ProviderCapabilitiesV2",
    "ProviderConfig",
    "ProviderConfigPayload",
    "ProviderConfigV2",
    "ProviderEditFilesV2",
    "ProviderEditSubmitV2",
    "ProviderPoll",
    "ProviderPollV2",
    "ProviderResult",
    "ProviderResultV2",
    "ProviderSubmit",
    "ProviderSubmitV2",
    "ResolvedCancel",
    "ResolvedEditSubmit",
    "ResolvedPoll",
    "ResolvedProviderConfig",
    "ResolvedResult",
    "ResolvedSubmit",
    "parse_provider_config",
    "provider_capabilities",
    "resolve_provider_config",
]
