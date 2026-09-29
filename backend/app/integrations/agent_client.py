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
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from ..core import settings as config
from ..core import validators as ssrf
from ..core.api_paths import RESPONSES_API_PATH
from ..core.redaction import redact_sensitive_text
from .assistant_client import normalize_assistant_api_path, validate_assistant_endpoint_async
from .json_client import json_headers
from .session_pool import TIMEOUT_PROMPT_OPTIMIZER, get_pool
from .upstream.errors import UpstreamApiError
from .upstream.transport import iter_bounded_sse_json_events, read_limited_text_response

logger = logging.getLogger(__name__)

ERROR_BODY_MAX_BYTES = 8 * 1024
MIN_TOTAL_TIMEOUT_SECONDS = 180.0
_TOOLS_UNSUPPORTED_HINTS = ("tool", "function", "stream")


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


# ── Neutral conversation items ──────────────────────────────────


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


# ── Neutral stream events ───────────────────────────────────────


@dataclass(frozen=True)
class TextDelta:
    text: str


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


AgentStreamEvent = TextDelta | ToolCallStarted | ToolCallComplete | Finish


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
    return payload


# ── Stream parsers ──────────────────────────────────────────────


def _error_message_from_event(event: dict[str, Any]) -> str | None:
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "Agent model returned an error")
    if isinstance(error, str) and error:
        return error
    response = event.get("response")
    if isinstance(response, dict) and response.get("status") == "failed":
        detail = response.get("error")
        if isinstance(detail, dict):
            return str(detail.get("message") or "Agent model response failed")
        return "Agent model response failed"
    if event.get("type") == "error":
        return str(event.get("message") or "Agent model returned an error")
    return None


class ChatStreamParser:
    """Turns chat/completions stream chunks into neutral events."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}
        self._finished = False
        self._finish_reason: str | None = None
        self._usage: dict[str, Any] = {}

    def feed(self, event: dict[str, Any]) -> list[AgentStreamEvent]:
        message = _error_message_from_event(event)
        if message:
            raise AgentClientError(redact_sensitive_text(message))
        out: list[AgentStreamEvent] = []
        if isinstance(event.get("usage"), dict):
            self._usage = event["usage"]
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return out
        choice = choices[0]
        delta = choice.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str) and content:
                out.append(TextDelta(content))
            for raw_call in delta.get("tool_calls") or []:
                if not isinstance(raw_call, dict):
                    continue
                index = int(raw_call.get("index") or 0)
                call = self._calls.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": "", "started": False},
                )
                if raw_call.get("id"):
                    call["id"] = str(raw_call["id"])
                function = raw_call.get("function")
                if isinstance(function, dict):
                    if function.get("name"):
                        call["name"] += str(function["name"])
                    if isinstance(function.get("arguments"), str):
                        call["arguments"] += function["arguments"]
                if call["name"] and not call["started"]:
                    call["started"] = True
                    call["id"] = call["id"] or f"call_{index}"
                    out.append(ToolCallStarted(call["id"], call["name"]))
        finish_reason = choice.get("finish_reason")
        if finish_reason:
            self._finish_reason = str(finish_reason)
            out.extend(self._complete_calls())
        return out

    def _complete_calls(self) -> list[AgentStreamEvent]:
        out: list[AgentStreamEvent] = []
        for index in sorted(self._calls):
            call = self._calls[index]
            if call.get("done") or not call["name"]:
                continue
            call["done"] = True
            call_id = call["id"] or f"call_{index}"
            if not call["started"]:
                out.append(ToolCallStarted(call_id, call["name"]))
            out.append(ToolCallComplete(call_id, call["name"], call["arguments"] or "{}"))
        return out

    def finish(self) -> list[AgentStreamEvent]:
        out = self._complete_calls()
        reason = self._finish_reason or "stop"
        out.append(Finish(stop_reason=reason, truncated=reason == "length", usage=self._usage))
        return out


class ResponsesStreamParser:
    """Turns /v1/responses stream events into neutral events."""

    def __init__(self) -> None:
        self._calls: dict[str, dict[str, Any]] = {}
        self._finish: Finish | None = None

    def _call_for(self, item: dict[str, Any]) -> dict[str, Any]:
        item_id = str(item.get("id") or item.get("call_id") or "")
        call = self._calls.setdefault(
            item_id,
            {"call_id": "", "name": "", "arguments": "", "started": False, "done": False},
        )
        if item.get("call_id"):
            call["call_id"] = str(item["call_id"])
        if item.get("name"):
            call["name"] = str(item["name"])
        if isinstance(item.get("arguments"), str) and item["arguments"]:
            call["arguments"] = item["arguments"]
        return call

    def _start(self, call: dict[str, Any]) -> list[AgentStreamEvent]:
        if call["started"] or not call["name"]:
            return []
        call["started"] = True
        call["call_id"] = call["call_id"] or f"call_{len(self._calls)}"
        return [ToolCallStarted(call["call_id"], call["name"])]

    def _complete(self, call: dict[str, Any]) -> list[AgentStreamEvent]:
        if call["done"] or not call["name"]:
            return []
        out = self._start(call)
        call["done"] = True
        out.append(ToolCallComplete(call["call_id"], call["name"], call["arguments"] or "{}"))
        return out

    def feed(self, event: dict[str, Any]) -> list[AgentStreamEvent]:
        message = _error_message_from_event(event)
        if message:
            raise AgentClientError(redact_sensitive_text(message))
        event_type = str(event.get("type") or "")
        out: list[AgentStreamEvent] = []
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                out.append(TextDelta(delta))
        elif event_type == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                out.extend(self._start(self._call_for(item)))
        elif event_type == "response.function_call_arguments.delta":
            item_id = str(event.get("item_id") or "")
            call = self._calls.get(item_id)
            if call is not None and isinstance(event.get("delta"), str):
                call["arguments"] += event["delta"]
        elif event_type == "response.function_call_arguments.done":
            item_id = str(event.get("item_id") or "")
            call = self._calls.get(item_id)
            if call is not None and isinstance(event.get("arguments"), str):
                call["arguments"] = event["arguments"]
        elif event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                call = self._call_for(item)
                if isinstance(item.get("arguments"), str):
                    call["arguments"] = item["arguments"]
                out.extend(self._complete(call))
        elif event_type in {"response.completed", "response.incomplete"}:
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            incomplete = event_type == "response.incomplete" or response.get("status") == "incomplete"
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            self._finish = Finish(
                stop_reason="length" if incomplete else "stop",
                truncated=incomplete,
                usage=usage,
            )
        return out

    def finish(self) -> list[AgentStreamEvent]:
        out: list[AgentStreamEvent] = []
        for call in self._calls.values():
            out.extend(self._complete(call))
        out.append(self._finish or Finish())
        return out


def parse_complete_chat_response(data: dict[str, Any]) -> list[AgentStreamEvent]:
    """Emit neutral events for a non-streamed chat/completions body."""
    message = _error_message_from_event(data)
    if message:
        raise AgentClientError(redact_sensitive_text(message))
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    body = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    out: list[AgentStreamEvent] = []
    content = body.get("content")
    if isinstance(content, str) and content:
        out.append(TextDelta(content))
    for index, raw_call in enumerate(body.get("tool_calls") or []):
        if not isinstance(raw_call, dict) or not isinstance(raw_call.get("function"), dict):
            continue
        call_id = str(raw_call.get("id") or f"call_{index}")
        name = str(raw_call["function"].get("name") or "")
        if not name:
            continue
        arguments = raw_call["function"].get("arguments")
        out.append(ToolCallStarted(call_id, name))
        out.append(ToolCallComplete(call_id, name, arguments if isinstance(arguments, str) and arguments else "{}"))
    reason = str(choice.get("finish_reason") or "stop")
    out.append(
        Finish(
            stop_reason=reason,
            truncated=reason == "length",
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else {},
        )
    )
    return out


def parse_complete_responses_response(data: dict[str, Any]) -> list[AgentStreamEvent]:
    """Emit neutral events for a non-streamed /v1/responses body."""
    message = _error_message_from_event(data)
    if message:
        raise AgentClientError(redact_sensitive_text(message))
    out: list[AgentStreamEvent] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    text = part.get("text")
                    if isinstance(text, str) and text:
                        out.append(TextDelta(text))
        elif item.get("type") == "function_call" and item.get("name"):
            call_id = str(item.get("call_id") or item.get("id") or f"call_{len(out)}")
            arguments = item.get("arguments")
            out.append(ToolCallStarted(call_id, str(item["name"])))
            out.append(
                ToolCallComplete(
                    call_id,
                    str(item["name"]),
                    arguments if isinstance(arguments, str) and arguments else "{}",
                )
            )
    incomplete = data.get("status") == "incomplete"
    out.append(
        Finish(
            stop_reason="length" if incomplete else "stop",
            truncated=incomplete,
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else {},
        )
    )
    return out


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
) -> AsyncIterator[AgentStreamEvent]:
    normalized_path = normalize_assistant_api_path(api_path)
    endpoint = await validate_assistant_endpoint_async(api_url, normalized_path)
    timeout_seconds = float(timeout_seconds or config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS)
    model = str(model or config.PROMPT_OPTIMIZER_MODEL).strip() or config.PROMPT_OPTIMIZER_MODEL
    use_responses = normalized_path == RESPONSES_API_PATH
    builder = build_responses_payload if use_responses else build_chat_payload
    payload = builder(
        model=model,
        instructions=instructions,
        items=items,
        tools=tools,
        tool_choice=tool_choice,
        max_output_tokens=max_output_tokens,
    )
    max_response_bytes = config.AI_ASSISTANT_MAX_RESPONSE_MB * 1024 * 1024
    timeout = aiohttp.ClientTimeout(
        total=max(timeout_seconds * 3, MIN_TOTAL_TIMEOUT_SECONDS),
        connect=min(timeout_seconds, 10.0),
        sock_connect=min(timeout_seconds, 10.0),
        sock_read=timeout_seconds,
    )
    try:
        session = get_pool().get(timeout_kind=TIMEOUT_PROMPT_OPTIMIZER)
        async with session.post(
            endpoint,
            json=payload,
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
