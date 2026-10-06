"""Version 1 mapping models: follow-up URLs come from upstream responses."""

from typing import Any, Literal, Optional

from pydantic import Field, field_validator, model_validator

from ...core.provider_mapping import ProviderMappingError, TEMPLATE_VARIABLES, template_variables, validate_template_shape
from ..common import StrictRequestModel
from ._common import _MODEL_PLACEHOLDER, _PLAIN_PATH, _check_config_size, _check_path


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
