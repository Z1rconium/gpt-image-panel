from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from ..core.api_paths import DEFAULT_IMAGE_MODEL
from ..core.image_models import MAX_PROMPT_CHARS, ImageQuality, is_image_25, validate_image_model_options
from ..core.validators import normalize_webhook_url
from .common import ApiPath, GenerateJobStatusValue, StrictRequestModel

def validate_image_size(size: str) -> str:
    if size == "auto":
        return size

    try:
        width_text, height_text = size.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except (AttributeError, ValueError):
        raise ValueError("size must be 'auto' or formatted as WIDTHxHEIGHT")

    if width <= 0 or height <= 0 or max(width, height) > 3840:
        raise ValueError("size width and height must be positive, with max side <= 3840")
    pixels = width * height
    aspect = max(width / height, height / width)
    if width % 16 != 0 or height % 16 != 0:
        raise ValueError("size width and height must be multiples of 16")
    if aspect > 3:
        raise ValueError("size aspect ratio must not exceed 3:1")
    if pixels < 655360 or pixels > 8294400:
        raise ValueError("size total pixels must be between 655360 and 8294400")

    return f"{width}x{height}"


class GenerateRequest(StrictRequestModel):
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)
    size: str = Field(default="auto", max_length=40)
    model: str = Field(default=DEFAULT_IMAGE_MODEL, max_length=200)
    n: int = Field(default=1, ge=1, le=10)
    quality: ImageQuality = "auto"
    output_format: Literal["png", "jpeg", "webp"] = "png"
    output_compression: Optional[int] = Field(default=None, ge=0, le=100)
    background: Literal["auto", "opaque", "transparent"] = "auto"
    response_format: Optional[Literal["url", "b64_json"]] = None
    webhook_url: Optional[str] = Field(default=None, max_length=2048)
    api_path: Optional[ApiPath] = None
    stream: bool = False
    partial_images: int = Field(default=2, ge=1, le=3)

    def normalize_model_options(self, api_path: str) -> None:
        # Call after resolving omitted model / response-format values from the preset.
        self.model = self.model.strip()
        validate_image_model_options(self.model, self.quality, api_path)
        if is_image_25(self.model):
            self.response_format = None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be empty")
        return value

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        return validate_image_size(value)

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: Optional[str]) -> Optional[str]:
        normalized = normalize_webhook_url(value)
        return normalized or None

    @model_validator(mode="after")
    def validate_output_options(self) -> "GenerateRequest":
        if self.output_format == "png":
            self.output_compression = None
        elif self.output_compression is None:
            self.output_compression = 100
        if self.background == "transparent" and self.output_format == "jpeg":
            self.output_format = "png"
            self.output_compression = None
        return self

    @model_validator(mode="after")
    def validate_streaming_options(self) -> "GenerateRequest":
        if self.stream and self.n > 1:
            raise ValueError("stream=true requires n=1; the image queue runs each n as a separate request")
        return self


class EditRequest(GenerateRequest):
    pass


class GenerateJobResponse(BaseModel):
    job_id: str
    status: GenerateJobStatusValue
    message: Optional[str] = None
    stage: Optional[str] = None
    operation: Optional[Literal["generation", "edit"]] = None


class UsageSummary(BaseModel):
    raw: dict = Field(default_factory=dict)
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    text_input_tokens: Optional[int] = None
    image_input_tokens: Optional[int] = None
    image_output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    available: bool = True


class CostEstimate(BaseModel):
    currency: str = "USD"
    estimated_cost_usd: Optional[float] = None
    rate_source: str = "unknown"
    pricing_model: Optional[str] = None
    complete: bool = False
    reason: Optional[str] = None


class GenerateJobImage(BaseModel):
    image_id: str
    image_url: str
    filename: str
    image_width: Optional[int] = None
    image_height: Optional[int] = None


class GeneratePreviewEvent(BaseModel):
    job_id: str
    unit_index: int = 0
    partial_image_index: int = 0
    sequence: int = 0
    mime_type: str = "image/png"
    data_url: str


class GenerateJobStatus(GenerateJobResponse):
    id: Optional[str] = None
    image_id: Optional[str] = None
    image_url: Optional[str] = None
    images: list[GenerateJobImage] = Field(default_factory=list)
    prompt: Optional[str] = None
    size: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    model: Optional[str] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    output_compression: Optional[int] = None
    background: Optional[str] = None
    response_format: Optional[str] = None
    n: Optional[int] = None
    completed_count: Optional[int] = Field(default=None, ge=0)
    success_count: Optional[int] = Field(default=None, ge=0)
    failure_count: Optional[int] = Field(default=None, ge=0)
    api_path: Optional[str] = None
    api_preset_name: Optional[str] = None
    duration: Optional[str] = None
    stage_timings: dict[str, float] = Field(default_factory=dict)
    usage: Optional[UsageSummary] = None
    cost: Optional[CostEstimate] = None
    streaming: Optional[bool] = None
    partial_images: Optional[int] = None
    error: Optional[str] = None
