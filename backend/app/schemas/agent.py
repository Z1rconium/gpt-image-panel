"""Agent conversation request and response DTOs."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from ..core import settings as config
from ..core.image_models import ImageQuality
from .common import ShortId, StrictRequestModel
from .generation import validate_image_size

# Static ceilings so absurd payloads fail before the configured caps are read.
AGENT_TEXT_CEILING_CHARS = 200_000
AGENT_ATTACHMENT_CEILING = 64

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
    text: str = Field(..., min_length=1, max_length=AGENT_TEXT_CEILING_CHARS)
    attachments: list[AgentAttachment] = Field(
        default_factory=list, max_length=AGENT_ATTACHMENT_CEILING
    )
    image_params: AgentImageParams = Field(default_factory=AgentImageParams)
    action: Literal["continue", "edit", "regenerate"] = "continue"
    source_turn_id: Optional[ShortId] = None
    branch_revision: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_branch_action(self) -> "AgentTurnRequest":
        if self.action != "continue" and (self.source_turn_id is None or self.branch_revision is None):
            raise ValueError("edit/regenerate require source_turn_id and branch_revision")
        return self

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        if len(value) > config.AGENT_MAX_USER_TEXT_CHARS:
            raise ValueError(f"text exceeds {config.AGENT_MAX_USER_TEXT_CHARS} characters")
        return value

    @field_validator("attachments")
    @classmethod
    def validate_attachments(cls, value: list[AgentAttachment]) -> list[AgentAttachment]:
        if len(value) > config.AGENT_MAX_ATTACHMENTS_PER_MESSAGE:
            raise ValueError(
                f"at most {config.AGENT_MAX_ATTACHMENTS_PER_MESSAGE} attachments are allowed"
            )
        return value


class AgentConversationSummary(BaseModel):
    id: str
    title: str
    message_count: int = 0
    turn_count: int = 0
    created_at: str
    updated_at: str
    active_turn_id: Optional[str] = None
    selected_turn_id: Optional[str] = None
    branch_revision: int = 0


class AgentBranchSelectRequest(StrictRequestModel):
    selected_turn_id: Optional[ShortId] = None
    expected_revision: int = Field(..., ge=0)


class AgentBranch(BaseModel):
    id: str
    parent_turn_id: Optional[str] = None
    round_no: int
    path_round_no: int
    preview: str
    status: AgentTurnStatusValue


class AgentConversationListResponse(BaseModel):
    items: list[AgentConversationSummary]


class AgentImageRef(BaseModel):
    ref_label: str
    round_no: int
    image_index: int
    role: Literal["input", "output"]
    image_id: Optional[str] = None
    filename: Optional[str] = None
    job_id: Optional[str] = None
    item_id: Optional[str] = None
    prompt: str = ""
    mode: str = "generate"
    status: AgentImageStatusValue
    error: Optional[str] = None
    deleted: bool = False
    message_id: str
    image_ref_id: Optional[str] = None
    turn_id: Optional[str] = None
    path_round_no: Optional[int] = None


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
    path_round_no: Optional[int] = None


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
    branches: list[AgentBranch] = Field(default_factory=list)


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
