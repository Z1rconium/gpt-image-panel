from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from ..core.api_paths import DEFAULT_IMAGE_MODEL
from ..core.image_models import MAX_PROMPT_CHARS, ImageQuality, is_image_25, is_image_26, validate_image_model_options
from ..core.validators import normalize_webhook_url
from .common import ApiPath, GenerateJobStatusValue, StrictRequestModel

def validate_image_size(size: str, *, animated: bool = False) -> str:
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
    if not animated and (pixels < 655360 or pixels > 8294400):
        raise ValueError("size total pixels must be between 655360 and 8294400")

    return f"{width}x{height}"


class GenerateRequest(StrictRequestModel):
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)
    size: str = Field(default="auto", max_length=40)
    model: str = Field(default=DEFAULT_IMAGE_MODEL, max_length=200)
    n: int = Field(default=1, ge=1, le=10)
    quality: ImageQuality = "auto"
    output_format: Literal["png", "jpeg", "webp", "svg", "gif"] = "png"
    complexity: Optional[Literal["low", "medium", "high"]] = None
    frame_count: Optional[int] = Field(default=None, ge=2, le=64)
    frame_duration_ms: Optional[int] = Field(default=None, ge=20, le=2000)
    output_compression: Optional[int] = Field(default=None, ge=0, le=100)
    background: Literal["auto", "opaque", "transparent", "chroma_green", "chroma_magenta"] = "auto"
    response_format: Optional[Literal["url", "b64_json"]] = None
    webhook_url: Optional[str] = Field(default=None, max_length=2048)
    api_path: Optional[ApiPath] = None
    api_preset_id: Optional[str] = Field(default=None, max_length=128)
    stream: bool = False
    partial_images: int = Field(default=2, ge=1, le=3)

    def normalize_model_options(self, api_path: str) -> None:
        # Call after resolving omitted model / response-format values from the preset.
        self.model = self.model.strip()
        validate_image_model_options(self.model, self.quality, api_path)
        if self.complexity is not None and not is_image_26(self.model):
            raise ValueError("complexity requires GPT Image 2.6")
        if self.output_format in {"svg", "gif"} and not is_image_26(self.model):
            raise ValueError("SVG/GIF requires GPT Image 2.6")
        if is_image_25(self.model) or is_image_26(self.model):
            self.response_format = None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be empty")
        return value


    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: Optional[str]) -> Optional[str]:
        normalized = normalize_webhook_url(value)
        return normalized or None

    @model_validator(mode="after")
    def validate_output_options(self) -> "GenerateRequest":
        self.size = validate_image_size(self.size, animated=self.output_format == "gif")
        if self.output_format != "gif" and (self.frame_count is not None or self.frame_duration_ms is not None):
            raise ValueError("frame fields are GIF-only")
        if self.output_format in {"svg", "gif"}:
            if self.stream or self.output_compression is not None or self.background.startswith("chroma_"):
                raise ValueError("SVG/GIF does not support streaming, compression or chroma backgrounds")
            if self.output_format == "svg" and self.n != 1:
                raise ValueError("SVG requires n=1")
            if self.output_format == "gif":
                self.frame_count = self.frame_count or 9
                self.frame_duration_ms = self.frame_duration_ms or 200
                if self.frame_duration_ms % 10:
                    raise ValueError("frame_duration_ms must be a multiple of 10")
            return self
        if self.output_format == "png":
            self.output_compression = None
        elif self.output_compression is None:
            self.output_compression = 100
        if self.background in {"chroma_green", "chroma_magenta"}:
            self.output_format = "png"
            self.output_compression = None
        elif self.background == "transparent" and self.output_format == "jpeg":
            self.output_format = "png"
            self.output_compression = None
        return self


class EditRequest(GenerateRequest):
    paste_back: Optional[bool] = None

    @model_validator(mode="after")
    def validate_edit_background(self) -> "EditRequest":
        # GIF edits are allowed (reference image -> animation); a mask is not part
        # of this schema, so the "no mask" half of that rule lives in queue_edit_job.
        if self.output_format == "svg":
            raise ValueError("SVG is generation-only")
        if self.background in {"chroma_green", "chroma_magenta"}:
            raise ValueError("Local chroma removal is only available for image generation")
        return self


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
    unit_index: Optional[int] = None
    result_index: Optional[int] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    sent_prompt: Optional[str] = None
    revised_prompt: Optional[str] = None
    reported_size: Optional[str] = None
    reported_quality: Optional[str] = None
    upstream_duration_ms: Optional[int] = None
    chroma_status: Optional[str] = None
    diagnostics: list[str] = Field(default_factory=list)
    paste_back: Optional[str] = None
    paste_back_scale: Optional[float] = None


class JobUnitRemoteSummary(BaseModel):
    phase: str = ""
    task_id: Optional[str] = None
    submitted_at: Optional[str] = None
    deadline_at: Optional[str] = None
    has_status_url: bool = False
    has_result_url: bool = False
    has_cancel_url: bool = False
    has_idempotency_key: bool = False


class JobUnitDiagnostics(BaseModel):
    unit_id: str
    unit_index: int
    status: str
    stage: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    recovery_count: int = 0
    remote: Optional[JobUnitRemoteSummary] = None
    diagnostics: Optional[dict] = None


class JobDiagnosticsResponse(BaseModel):
    job_id: str
    units: list[JobUnitDiagnostics] = Field(default_factory=list)


class GenerateJobStatus(GenerateJobResponse):
    id: Optional[str] = None
    image_id: Optional[str] = None
    image_url: Optional[str] = None
    images: list[GenerateJobImage] = Field(default_factory=list)
    unit_statuses: dict[str, str] = Field(default_factory=dict)
    agent_turn_id: Optional[str] = None
    agent_conversation_id: Optional[str] = None
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
    complexity: Optional[str] = None
    frame_count: Optional[int] = None
    frame_duration_ms: Optional[int] = None
    markup_applied: Optional[bool] = None
    background: Optional[str] = None
    response_format: Optional[str] = None
    n: Optional[int] = None
    completed_count: Optional[int] = Field(default=None, ge=0)
    success_count: Optional[int] = Field(default=None, ge=0)
    failure_count: Optional[int] = Field(default=None, ge=0)
    api_path: Optional[str] = None
    api_preset_id: Optional[str] = None
    api_preset_name: Optional[str] = None
    duration: Optional[str] = None
    stage_timings: dict[str, float] = Field(default_factory=dict)
    usage: Optional[UsageSummary] = None
    cost: Optional[CostEstimate] = None
    streaming: Optional[bool] = None
    partial_images: Optional[int] = None
    mask_applied: Optional[bool] = None
    paste_back: Optional[bool] = None
    error: Optional[str] = None
