import json
import re
from typing import Any, Literal, Optional

from pydantic import Field, field_validator, model_validator

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
_PLAIN_PATH = re.compile(r"^(/[A-Za-z0-9._~\-/]*)?$")


def _check_path(path: str, label: str) -> str:
    try:
        parse_json_path(path)
    except ProviderMappingError as exc:
        raise ValueError(f"{label}: {exc}") from exc
    return path.strip()


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
    version: Literal[1] = 1
    auth: ProviderAuth = Field(default_factory=ProviderAuth)
    submit: ProviderSubmit
    poll: ProviderPoll
    result: ProviderResult
    cancel: Optional[ProviderCancel] = None

    @model_validator(mode="after")
    def validate_size(self) -> "ProviderConfig":
        if len(json.dumps(self.model_dump(mode="json")).encode("utf-8")) > MAX_PROVIDER_CONFIG_BYTES:
            raise ValueError("provider_config is too large")
        return self
