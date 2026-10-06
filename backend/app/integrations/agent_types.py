"""Neutral conversation items, stream events and errors of the Agent model client."""

from dataclasses import dataclass, field
from typing import Any

from .agent_search import SearchStatus, SourceCitation


class AgentClientError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class AgentTimeoutError(AgentClientError):
    def __init__(self, message: str = "Agent model request timed out"):
        super().__init__(message, status=504)


class AgentToolsUnsupportedError(AgentClientError):
    def __init__(self, message: str = "This endpoint does not support tool calling"):
        super().__init__(message, status=422)


@dataclass(frozen=True)
class UserItem:
    text: str
    images: tuple[str, ...] = ()  # data: URLs


@dataclass(frozen=True)
class AssistantTextItem:
    text: str


@dataclass(frozen=True)
class ToolCallItem:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ToolResultItem:
    call_id: str
    output: str


AgentItem = UserItem | AssistantTextItem | ToolCallItem | ToolResultItem


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class TextDelta:
    text: str
    item_id: str = ""
    content_index: int = 0


@dataclass(frozen=True)
class ToolCallStarted:
    call_id: str
    name: str


@dataclass(frozen=True)
class ToolCallComplete:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class Finish:
    stop_reason: str = "stop"
    truncated: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    # False when the stream ended without a protocol terminator (EOF).
    complete: bool = True


AgentStreamEvent = TextDelta | ToolCallStarted | ToolCallComplete | Finish | SearchStatus | SourceCitation
