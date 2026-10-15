"""AI Assistant and Agent runtime endpoint resolution (F-G6-1).

JSON transport/response shaping lives in
:mod:`app.services.assistant_json`; global request-slot leasing lives in
:mod:`app.services.assistant_slots`. This module resolves the configured
endpoint (and the Agent-specific knobs) into an immutable runtime snapshot.
"""

import contextvars
import logging
from dataclasses import dataclass
from typing import Any, Literal

from ..core import secrets
from ..core.errors import InvalidRequestError
from ..integrations import assistant_client
from ..repositories.db.execution import run_db_operation
from ..schemas.settings import AIAssistantSettingsRequest
from . import presets

logger = logging.getLogger(__name__)

ASSISTANT_IMAGE_UPLOAD_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class AssistantRuntime:
    api_url: str
    api_key: str
    api_path: str
    endpoint: str
    model: str
    timeout_seconds: int


@dataclass
class AssistantRuntimeHolder:
    runtime: AssistantRuntime | None = None


_batch_assistant_runtime: contextvars.ContextVar[AssistantRuntimeHolder | None] = contextvars.ContextVar(
    "batch_assistant_runtime", default=None
)
_batch_target_language: contextvars.ContextVar[Literal["en", "zh-CN"]] = contextvars.ContextVar(
    "batch_target_language", default="en"
)

AI_ANALYZE_JOB_KIND = "ai_analyze"
GALLERY_SELECTION_TOKEN_KIND = "batch_selection"
MAX_ACTIVE_AI_ANALYZE_JOBS = 1
AI_ANALYZE_JOB_LEASE_SECONDS = 300
AI_ANALYZE_JOB_LEASE_RENEW_SECONDS = 60
AI_ANALYZE_JOB_BATCH_SIZE = 25


class AIAnalyzeJobLeaseLost(RuntimeError):
    pass


AGENT_SNAPSHOT_VERSION = 1


def _assistant_settings(draft: AIAssistantSettingsRequest | None = None) -> dict:
    settings = presets.get_ai_assistant_settings()
    if draft is not None:
        settings = presets.apply_ai_assistant_settings(settings, draft)
    return settings


def _resolve_runtime(
    *, vision: bool = False, agent: bool = False, settings: dict | None = None
) -> AssistantRuntime:
    settings = presets.effective_ai_assistant_settings(settings if settings is not None else _assistant_settings())
    if not settings.get("enabled"):
        raise InvalidRequestError("AI Assistant is not enabled")
    if agent and not settings.get("agent_enabled"):
        raise InvalidRequestError("Agent mode is not enabled")
    api_url = str(settings.get("api_url") or "").strip()
    if not api_url:
        raise InvalidRequestError("Prompt Optimizer endpoint URL is not configured for AI Assistant")
    api_key = presets.resolve_ai_assistant_api_key(settings)
    if not api_key:
        raise InvalidRequestError("Prompt Optimizer API key is not configured for AI Assistant")
    api_path = assistant_client.normalize_assistant_api_path(settings.get("api_path"))
    try:
        endpoint = assistant_client.validate_assistant_endpoint(api_url, api_path)
    except ValueError as e:
        raise InvalidRequestError(str(e)) from e
    model_key = "agent_model" if agent else "vision_model" if vision else "model"
    model = str(settings.get(model_key) or settings.get("model") or "").strip()
    if not model:
        raise InvalidRequestError("AI Assistant model is not configured")
    timeout_seconds = int(settings.get("timeout_seconds") or 60)
    return AssistantRuntime(api_url, api_key, api_path, endpoint, model, timeout_seconds)


async def _resolve_runtime_async(*, vision: bool = False, settings: dict | None = None) -> AssistantRuntime:
    return await run_db_operation(_resolve_runtime, vision=vision, settings=settings)


@dataclass(frozen=True)
class AgentRuntime:
    """Endpoint plus the Agent-specific knobs, resolved from the AI Assistant settings."""

    assistant: AssistantRuntime
    max_tool_rounds: int
    system_prompt: str
    web_search_enabled: bool = False
    web_search_supported: bool = False


def agent_execution_snapshot(agent: AgentRuntime) -> dict[str, Any]:
    """Credential-free description of what an accepted turn will run against."""
    runtime = agent.assistant
    return {
        "version": AGENT_SNAPSHOT_VERSION,
        "assistant": {
            "api_url": runtime.api_url,
            "api_path": runtime.api_path,
            "model": runtime.model,
            "timeout_seconds": runtime.timeout_seconds,
        },
        "max_tool_rounds": agent.max_tool_rounds,
        "system_prompt": agent.system_prompt,
        "web_search": {"enabled": agent.web_search_enabled, "supported": agent.web_search_supported},
    }


def resolve_agent_runtime(settings: dict | None = None, snapshot: dict | None = None) -> AgentRuntime:
    """Resolve the Agent endpoint, optionally pinned to a turn's execution snapshot.

    The credential is always resolved live, but only for the snapshot's origin:
    if the configured endpoint moved elsewhere the turn fails instead of being
    silently sent to the new target. A legacy (empty) snapshot uses live settings.
    """
    effective = presets.effective_ai_assistant_settings(
        settings if settings is not None else _assistant_settings()
    )
    runtime = _resolve_runtime(agent=True, settings=effective)
    agent = AgentRuntime(
        assistant=runtime,
        max_tool_rounds=int(effective.get("agent_max_tool_rounds") or 1),
        system_prompt=str(effective.get("agent_system_prompt") or ""),
        web_search_enabled=bool(effective.get("agent_web_search_enabled", False)),
        web_search_supported=bool(effective.get("agent_web_search_supported", False)),
    )
    pinned = snapshot.get("assistant") if isinstance(snapshot, dict) else None
    if snapshot is None or not isinstance(pinned, dict) or snapshot.get("version") != AGENT_SNAPSHOT_VERSION:
        return agent
    try:
        same_origin = secrets.same_origin(runtime.api_url, str(pinned.get("api_url") or ""))
    except Exception:
        same_origin = False
    if not same_origin:
        raise InvalidRequestError(
            "The Agent endpoint changed after this turn was accepted. Start a new turn to use the new endpoint."
        )
    try:
        endpoint = assistant_client.validate_assistant_endpoint(
            str(pinned["api_url"]), str(pinned["api_path"])
        )
    except (KeyError, ValueError) as error:
        raise InvalidRequestError(str(error)) from error
    search = snapshot.get("web_search") if isinstance(snapshot.get("web_search"), dict) else {}
    return AgentRuntime(
        assistant=AssistantRuntime(
            str(pinned["api_url"]),
            runtime.api_key,
            str(pinned["api_path"]),
            endpoint,
            str(pinned["model"]),
            int(pinned.get("timeout_seconds") or runtime.timeout_seconds),
        ),
        max_tool_rounds=int(snapshot.get("max_tool_rounds") or agent.max_tool_rounds),
        system_prompt=str(snapshot.get("system_prompt", agent.system_prompt)),
        web_search_enabled=bool(search.get("enabled", False)),
        web_search_supported=bool(search.get("supported", False)),
    )


async def resolve_agent_runtime_async(
    settings: dict | None = None, snapshot: dict | None = None
) -> AgentRuntime:
    return await run_db_operation(resolve_agent_runtime, settings, snapshot)


__all__ = [name for name in globals() if not name.startswith("__")]
