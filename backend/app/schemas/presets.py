from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from ..core.validators import (
    normalize_secret_env_ref_or_plaintext,
    normalize_upstream_base_url,
)
from .provider import ProviderConfigPayload
from .common import (
    ApiKeySource,
    ApiPath,
    PresetHealthStatus,
    ProviderKind,
    ResponseFormatDefault,
    StrictRequestModel,
)


class ApiPresetResponse(BaseModel):
    id: str
    name: str
    api_url: str
    api_path: ApiPath
    default_model: str
    default_response_format: ResponseFormatDefault = "url"
    supports_mask: bool = True
    prompt_guard: bool = False
    provider_kind: ProviderKind = "openai"
    provider_config: Optional[ProviderConfigPayload] = None
    provider_capabilities: dict[str, Any] = Field(default_factory=dict)
    api_key_masked: str
    has_api_key: bool
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None
    api_key_secret_id: Optional[str] = None


class PresetCreateRequest(StrictRequestModel):
    name: Optional[str] = Field(default=None, max_length=160)
    api_url: Optional[str] = Field(default=None, max_length=2048)
    api_key: Optional[str] = Field(
        default=None,
        max_length=8192,
        description=(
            "Opaque secret_id predeclared in the startup Secret Registry."
        ),
    )
    api_path: Optional[ApiPath] = None
    default_model: Optional[str] = Field(default=None, max_length=200)
    default_response_format: Optional[ResponseFormatDefault] = None
    supports_mask: Optional[bool] = None
    prompt_guard: Optional[bool] = None
    provider_kind: Optional[ProviderKind] = None
    provider_config: Optional[ProviderConfigPayload] = None
    source_preset_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("api_url")
    @classmethod
    def validate_optional_api_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_upstream_base_url(value)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="API key",
        )


class PresetHealthCheck(BaseModel):
    name: str
    status: PresetHealthStatus
    message: str


class PresetHealthResponse(BaseModel):
    status: PresetHealthStatus
    checks: list[PresetHealthCheck]


class CredentialProbeRequest(StrictRequestModel):
    use_credentials: bool = False


PRESET_EXPORT_FORMAT = "gpt-image-panel-presets"
PRESET_EXPORT_VERSION = 1
MAX_PRESET_IMPORT_BYTES = 256 * 1024
MAX_IMPORTED_PRESETS = 50


class PresetExportItem(StrictRequestModel):
    name: str = Field(min_length=1, max_length=160)
    api_url: str = Field(max_length=2048)
    api_path: ApiPath = "/v1/images/generations"
    default_model: str = Field(default="", max_length=200)
    default_response_format: ResponseFormatDefault = "url"
    supports_mask: bool = True
    prompt_guard: bool = False
    provider_kind: ProviderKind = "openai"
    provider_config: Optional[ProviderConfigPayload] = None

    @field_validator("api_url")
    @classmethod
    def validate_export_api_url(cls, value: str) -> str:
        return normalize_upstream_base_url(value)


class PresetExportPackage(StrictRequestModel):
    format: Literal["gpt-image-panel-presets"] = "gpt-image-panel-presets"
    format_version: Literal[1] = 1
    exported_at: Optional[str] = None
    presets: list[PresetExportItem] = Field(min_length=1, max_length=MAX_IMPORTED_PRESETS)


class PresetExportRequest(StrictRequestModel):
    preset_id: str = Field(min_length=1, max_length=128)


class PresetImportIssue(BaseModel):
    path: str
    message: str


class PresetImportPreviewItem(BaseModel):
    index: int
    name: str
    api_url: str
    provider_kind: str
    duplicate_preset_id: Optional[str] = None
    duplicate_preset_name: Optional[str] = None
    will_reuse_api_key: bool = False
    warnings: list[str] = Field(default_factory=list)


class PresetImportPreviewResponse(BaseModel):
    valid: bool
    errors: list[PresetImportIssue] = Field(default_factory=list)
    items: list[PresetImportPreviewItem] = Field(default_factory=list)
    total_bytes: int = 0


class PresetImportPreviewRequest(StrictRequestModel):
    package: Any = None


class PresetImportApplyItem(StrictRequestModel):
    index: int = Field(ge=0, le=MAX_IMPORTED_PRESETS - 1)
    action: Literal["create", "update", "skip"] = "create"
    target_preset_id: Optional[str] = Field(default=None, max_length=128)


class PresetImportApplyRequest(StrictRequestModel):
    package: Any = None
    items: list[PresetImportApplyItem] = Field(default_factory=list, max_length=MAX_IMPORTED_PRESETS)


class PresetOrderRequest(StrictRequestModel):
    preset_ids: list[str] = Field(min_length=1, max_length=MAX_IMPORTED_PRESETS)
