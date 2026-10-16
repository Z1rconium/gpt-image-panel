from typing import Any, Optional

from pydantic import BaseModel, Field

from .common import StrictRequestModel
from .presets import PresetImportIssue


class ProviderMappingValidateRequest(StrictRequestModel):
    provider_config: Any = None
    sample_submit: Any = None
    sample_poll: Any = None
    sample_result: Any = None


class ProviderMappingFieldResult(BaseModel):
    found: bool = False
    value: Optional[str] = None
    source: Optional[str] = None
    detail: Optional[str] = None


class ProviderMappingExtraction(BaseModel):
    task_id: ProviderMappingFieldResult
    status_url: ProviderMappingFieldResult
    result_url: ProviderMappingFieldResult
    status_value: ProviderMappingFieldResult
    image_count: Optional[int] = None
    image_kind: Optional[str] = None
    image_samples: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProviderMappingValidateResponse(BaseModel):
    valid: bool
    errors: list[PresetImportIssue] = Field(default_factory=list)
    extraction: Optional[ProviderMappingExtraction] = None
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ProviderMappingPromptResponse(BaseModel):
    prompt: str
