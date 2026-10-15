from typing import Optional

from pydantic import BaseModel, Field, model_validator

from .common import (
    OverallConfigValueSource,
    OverallConfigValueType,
    StrictRequestModel,
)


class OverallConfigItem(BaseModel):
    name: str
    type: OverallConfigValueType
    group: str
    description: str
    value: str | bool | int | float
    value_masked: str
    env_value_masked: str
    override_value_masked: Optional[str] = None
    source: OverallConfigValueSource
    is_env_set: bool
    has_override: bool
    secret: bool = False
    hot_reload: bool = True
    restart_required: bool = False
    build_only: bool = False
    startup_only: bool = False
    updated_at: Optional[str] = None
    override_updated_at: Optional[str] = None


class OverallConfigResponse(BaseModel):
    items: list[OverallConfigItem]
    restart_required_names: list[str] = Field(default_factory=list)


class OverallConfigUpdateItem(StrictRequestModel):
    name: str = Field(..., min_length=1, max_length=128)
    value: str | bool | int | float | None = None
    clear_override: bool = False

    @model_validator(mode="after")
    def validate_action(self):
        if self.clear_override and self.value is not None:
            raise ValueError("clear_override cannot be combined with value")
        return self


class OverallConfigUpdateRequest(StrictRequestModel):
    updates: list[OverallConfigUpdateItem] = Field(default_factory=list, max_length=128)
