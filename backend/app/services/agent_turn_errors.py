"""Control-flow exceptions shared by the Agent turn runner and its tool executor."""

import logging

from ..core.errors import DomainError
from ..core.redaction import redact_sensitive_text
from ..integrations.agent_client import AgentClientError, AgentTimeoutError, AgentToolsUnsupportedError

logger = logging.getLogger(__name__)
TOOLS_UNSUPPORTED_MESSAGE = (
    "This endpoint or model does not support tool calling. "
    "Choose a model that supports function calling in Settings."
)


class TurnCancelled(Exception):
    """The user asked to stop this turn."""


class TurnFailed(Exception):
    """The turn cannot continue; the message is shown to the user."""


def describe_failure(error: BaseException) -> str:
    if isinstance(error, TurnFailed):
        return str(error)
    if isinstance(error, AgentToolsUnsupportedError):
        return TOOLS_UNSUPPORTED_MESSAGE
    if isinstance(error, AgentTimeoutError):
        return "The model request timed out."
    if isinstance(error, AgentClientError):
        return redact_sensitive_text(str(error)) or "The model request failed."
    if isinstance(error, DomainError):
        return redact_sensitive_text(str(error.detail or error)) or "The Agent turn failed."
    logger.error("Agent turn failed unexpectedly", exc_info=error)
    return "The Agent turn failed unexpectedly."

