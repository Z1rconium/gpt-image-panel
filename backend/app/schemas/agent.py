"""Agent conversation request and response DTOs."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from ..core.image_models import ImageQuality
from .common import ShortId, StrictRequestModel
from .generation import validate_image_size

AgentTurnStatusValue = Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
AgentMessageStatusValue = Literal["streaming", "complete", "failed", "cancelled", "interrupted"]
AgentImageStatusValue = Literal["pending", "succeeded", "failed", "cancelled"]


class AgentConversationCreateRequest(StrictRequestModel):
    title: str = Field(default="", max_length=200)


class AgentConversationRenameRequest(StrictRequestModel):
    title: str = Field(..., min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be empty")
        return value


class AgentAttachment(StrictRequestModel):
    kind: Literal["gallery"] = "gallery"
    image_id: ShortId


class AgentImageParams(StrictRequestModel):
    size: str = Field(default="auto", max_length=40)
    quality: ImageQuality = "auto"
    output_format: Literal["png", "jpeg", "webp"] = "png"

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        return validate_image_size(value)


class AgentTurnRequest(StrictRequestModel):
    client_turn_id: str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(..., min_length=1)
    attachments: list[AgentAttachment] = Field(default_factory=list)
    image_params: AgentImageParams = Field(default_factory=AgentImageParams)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        return value


class AgentConversationSummary(BaseModel):
    id: str
    title: str
    message_count: int = 0
    turn_count: int = 0
    created_at: str
    updated_at: str
    active_turn_id: Optional[str] = None


class AgentConversationListResponse(BaseModel):
    items: list[AgentConversationSummary]


class AgentImageRef(BaseModel):
    ref_label: str
    round_no: int
    image_index: int
    role: Literal["input", "output"]
    image_id: Optional[str] = None
    job_id: Optional[str] = None
    item_id: Optional[str] = None
    prompt: str = ""
    mode: str = "generate"
    status: AgentImageStatusValue
    error: Optional[str] = None
    deleted: bool = False
    message_id: str


class AgentMessage(BaseModel):
    id: str
    turn_id: str
    seq: int
    round_no: int
    role: Literal["user", "assistant"]
    text: str = ""
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    status: AgentMessageStatusValue
    created_at: str
    updated_at: str


class AgentActiveTurn(BaseModel):
    id: str
    status: AgentTurnStatusValue
    round_no: int


class AgentConversationDetail(BaseModel):
    conversation: AgentConversationSummary
    messages: list[AgentMessage]
    image_refs: list[AgentImageRef]
    active_turn: Optional[AgentActiveTurn] = None
    has_more: bool = False


class AgentTurnAccepted(BaseModel):
    turn_id: str
    conversation_id: str
    round_no: int
    status: AgentTurnStatusValue
    user_message_id: str
    assistant_message_id: str
    replayed: bool = False


class AgentTurnStatus(BaseModel):
    turn_id: str
    conversation_id: str
    round_no: int
    status: AgentTurnStatusValue
    rounds_used: int = 0
    error_message: Optional[str] = None
