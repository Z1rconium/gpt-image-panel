"""Version 2 mapping models: templated URLs, transports, edit support and capabilities."""

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BeforeValidator, Field, field_validator, model_validator

from ...core.provider_mapping import ProviderMappingError, TEMPLATE_VARIABLES, template_variables, validate_template_shape
from ..common import StrictRequestModel
from ._common import EDIT_TEMPLATE_VARIABLES, _FORM_FIELD, _MODEL_PLACEHOLDER, _PLAIN_PATH, _TOKEN, _check_config_size, _check_path, _check_query, _check_url_template
from .v1 import ProviderAuth, ProviderConfig, ProviderSubmit


RESERVED_IDEMPOTENCY_HEADERS = frozenset(
    {
        "authorization", "proxy-authorization", "x-api-key", "api-key", "x-auth-token", "cookie", "set-cookie",
        "host", "content-length", "content-type", "content-encoding", "transfer-encoding", "connection",
        "keep-alive", "te", "trailer", "upgrade", "expect", "user-agent", "proxy-connection",
    }
)


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
        if value.lower() in RESERVED_IDEMPOTENCY_HEADERS:
            raise ValueError("submit.idempotency_header cannot be an authentication or transport header")
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
    here. ``stream`` must stay false: mapped providers speak the declarative
    HTTP mapping, not the SSE protocols the panel streams previews over.
    """

    stream: Literal[False] = Field(
        default=False,
        description="Streamed previews need an SSE-capable OpenAI-compatible API; mapped providers must keep this false.",
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
