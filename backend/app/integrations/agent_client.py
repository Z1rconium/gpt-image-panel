"""Multi-turn, tool-calling LLM client for Agent conversations.

The Agent reuses the AI Assistant endpoint, which speaks either
``/v1/chat/completions`` or ``/v1/responses``. This module hides the difference
behind neutral input items and stream events; it never touches the database or
FastAPI, so the turn runner can be tested with a scripted fake.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import aiohttp

from ..core import settings as config
from ..core import validators as ssrf
from ..core.api_paths import RESPONSES_API_PATH
from ..core.redaction import redact_sensitive_text
from .assistant_client import normalize_assistant_api_path, validate_assistant_endpoint_async
from .agent_types import (  # noqa: F401  (re-exported)
    AgentClientError,
    AgentTimeoutError,
    AgentToolsUnsupportedError,
    UserItem,
    AssistantTextItem,
    ToolCallItem,
    ToolResultItem,
    ToolSpec,
    TextDelta,
    ToolCallStarted,
    ToolCallComplete,
    Finish,
    AgentItem,
    AgentStreamEvent,
)
from .agent_parsers import (  # noqa: F401  (re-exported)
    ChatStreamParser,
    ResponsesStreamParser,
    _error_message_from_event,
    parse_complete_chat_response,
    parse_complete_responses_response,
)
from .json_client import json_headers
from .session_pool import TIMEOUT_PROMPT_OPTIMIZER, get_pool
from .upstream.errors import UpstreamApiError
from .upstream.transport import iter_bounded_sse_json_events, read_limited_text_response

logger = logging.getLogger(__name__)

ERROR_BODY_MAX_BYTES = 8 * 1024
MIN_TOTAL_TIMEOUT_SECONDS = 180.0


def request_total_timeout_seconds(timeout_seconds: float) -> float:
    """Total request deadline; the assistant slot lease is derived from this."""
    return max(float(timeout_seconds) * 3, MIN_TOTAL_TIMEOUT_SECONDS)
_TOOLS_UNSUPPORTED_HINTS = ("tool", "function", "stream")








# ── Neutral conversation items ──────────────────────────────────














# ── Neutral stream events ───────────────────────────────────────












# ── Payload builders ────────────────────────────────────────────


def _data_url_parts(urls: Sequence[str]) -> list[str]:
    return [url for url in urls if isinstance(url, str) and url]


def build_chat_payload(
    *,
    model: str,
    instructions: str,
    items: Sequence[AgentItem],
    tools: Sequence[ToolSpec],
    tool_choice: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": instructions}]
    pending: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        if not pending.get("tool_calls"):
            pending.pop("tool_calls", None)
        messages.append(pending)
        pending = None

    for item in items:
        if isinstance(item, UserItem):
            flush()
            images = _data_url_parts(item.images)
            if images:
                content: str | list[dict[str, Any]] = [{"type": "text", "text": item.text}]
                for url in images:
                    content.append({"type": "image_url", "image_url": {"url": url, "detail": "low"}})
            else:
                content = item.text
            messages.append({"role": "user", "content": content})
        elif isinstance(item, AssistantTextItem):
            flush()
            pending = {"role": "assistant", "content": item.text, "tool_calls": []}
        elif isinstance(item, ToolCallItem):
            if pending is None:
                pending = {"role": "assistant", "content": None, "tool_calls": []}
            pending["tool_calls"].append(
                {
                    "id": item.call_id,
                    "type": "function",
                    "function": {"name": item.name, "arguments": item.arguments_json or "{}"},
                }
            )
        elif isinstance(item, ToolResultItem):
            flush()
            messages.append({"role": "tool", "tool_call_id": item.call_id, "content": item.output})
    flush()

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_output_tokens,
        "stream": True,
    }
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
            }
            for tool in tools
        ]
        payload["tool_choice"] = tool_choice
    return payload


def build_responses_payload(
    *,
    model: str,
    instructions: str,
    items: Sequence[AgentItem],
    tools: Sequence[ToolSpec],
    tool_choice: str,
    max_output_tokens: int,
    web_search: bool = False,
    max_tool_calls: int = 4,
) -> dict[str, Any]:
    input_items: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, UserItem):
            content: list[dict[str, Any]] = [{"type": "input_text", "text": item.text}]
            for url in _data_url_parts(item.images):
                content.append({"type": "input_image", "image_url": url})
            input_items.append({"role": "user", "content": content})
        elif isinstance(item, AssistantTextItem):
            input_items.append(
                {"role": "assistant", "content": [{"type": "output_text", "text": item.text}]}
            )
        elif isinstance(item, ToolCallItem):
            input_items.append(
                {
                    "type": "function_call",
                    "call_id": item.call_id,
                    "name": item.name,
                    "arguments": item.arguments_json or "{}",
                }
            )
        elif isinstance(item, ToolResultItem):
            input_items.append(
                {"type": "function_call_output", "call_id": item.call_id, "output": item.output}
            )

    payload: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "max_output_tokens": max_output_tokens,
        "stream": True,
    }
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools
        ]
        payload["tool_choice"] = tool_choice
    if web_search:
        payload.setdefault("tools", []).append({"type": "web_search"})
        payload["tool_choice"] = tool_choice
        payload["max_tool_calls"] = max(1, min(64, max_tool_calls))
    return payload


# ── Stream parsers ──────────────────────────────────────────────












# ── Transport ───────────────────────────────────────────────────


def _looks_like_tools_unsupported(status: int, body_text: str) -> bool:
    if status not in {400, 404, 405, 415, 422}:
        return False
    lowered = body_text.lower()
    return any(hint in lowered for hint in _TOOLS_UNSUPPORTED_HINTS)


async def stream_agent_response(
    *,
    api_url: str,
    api_key: str,
    api_path: str,
    model: str,
    instructions: str,
    items: Sequence[AgentItem],
    tools: Sequence[ToolSpec],
    tool_choice: str = "auto",
    timeout_seconds: float | None = None,
    max_output_tokens: int = 4096,
    web_search: bool = False,
    max_tool_calls: int = 4,
) -> AsyncIterator[AgentStreamEvent]:
    normalized_path = normalize_assistant_api_path(api_path)
    endpoint = await validate_assistant_endpoint_async(api_url, normalized_path)
    timeout_seconds = float(timeout_seconds or config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS)
    model = str(model or config.PROMPT_OPTIMIZER_MODEL).strip() or config.PROMPT_OPTIMIZER_MODEL
    use_responses = normalized_path == RESPONSES_API_PATH
    if web_search and not use_responses:
        raise AgentToolsUnsupportedError("Web search requires a Responses endpoint")
    builder = build_responses_payload if use_responses else build_chat_payload
    payload = builder(
        model=model,
        instructions=instructions,
        items=items,
        tools=tools,
        tool_choice=tool_choice,
        max_output_tokens=max_output_tokens,
        **({"web_search": True, "max_tool_calls": max_tool_calls} if web_search else {}),
    )
    max_response_bytes = config.AI_ASSISTANT_MAX_RESPONSE_MB * 1024 * 1024
    timeout = aiohttp.ClientTimeout(
        total=request_total_timeout_seconds(timeout_seconds),
        connect=min(timeout_seconds, 10.0),
        sock_connect=min(timeout_seconds, 10.0),
        sock_read=timeout_seconds,
    )
    # The payload can carry megabytes of base64 image data, so it is encoded
    # off the event loop; aiohttp's json= would serialize it inline.
    body = await asyncio.to_thread(
        lambda: json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    try:
        session = get_pool().get(timeout_kind=TIMEOUT_PROMPT_OPTIMIZER)
        async with session.post(
            endpoint,
            data=body,
            headers=json_headers(api_key),
            allow_redirects=False,
            timeout=timeout,
        ) as resp:
            try:
                ssrf.validate_response_peer_ip(resp, "Agent endpoint")
            except ValueError as e:
                raise AgentClientError(str(e), status=502) from e
            if resp.status != 200:
                error_text = ""
                try:
                    error_text = await read_limited_text_response(
                        resp, ERROR_BODY_MAX_BYTES, label="Agent error response"
                    )
                except (UpstreamApiError, aiohttp.ClientError, UnicodeDecodeError):
                    pass
                logger.warning("Agent upstream error: status=%d", resp.status)
                if tools and _looks_like_tools_unsupported(resp.status, error_text):
                    raise AgentToolsUnsupportedError()
                raise AgentClientError(
                    f"Agent endpoint returned HTTP {resp.status}", status=resp.status
                )

            content_type = resp.headers.get("Content-Type", "").lower()
            if "text/event-stream" in content_type:
                parser = ResponsesStreamParser() if use_responses else ChatStreamParser()
                try:
                    async for event in iter_bounded_sse_json_events(
                        resp,
                        max_total_bytes=max_response_bytes,
                        max_frame_bytes=min(
                            config.STREAMING_MAX_FRAME_MB * 1024 * 1024, max_response_bytes
                        ),
                        label="Agent response stream",
                    ):
                        if not isinstance(event, dict):
                            continue
                        for neutral in parser.feed(event):
                            yield neutral
                except UpstreamApiError as e:
                    raise AgentClientError(str(e)) from e
                for neutral in parser.finish():
                    yield neutral
                return

            # The gateway ignored `stream`; treat the body as a complete response.
            try:
                body = await read_limited_text_response(
                    resp, max_response_bytes, label="Agent response"
                )
                data = json.loads(body)
            except UpstreamApiError as e:
                raise AgentClientError(str(e)) from e
            except ValueError as e:
                raise AgentClientError("Agent endpoint returned a non-JSON response") from e
            if not isinstance(data, dict):
                raise AgentClientError("Agent endpoint returned an unexpected response")
            parse = parse_complete_responses_response if use_responses else parse_complete_chat_response
            for neutral in parse(data):
                yield neutral
    except (aiohttp.ServerTimeoutError, TimeoutError, asyncio.TimeoutError) as e:
        raise AgentTimeoutError() from e
    except aiohttp.ClientError as e:
        raise AgentClientError(
            redact_sensitive_text(f"Agent endpoint connection error: {e}")
        ) from e
