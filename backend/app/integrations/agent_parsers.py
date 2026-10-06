"""Chat and Responses stream parsers: model protocol events to neutral Agent events."""

from typing import Any

from ..core.redaction import redact_sensitive_text
from .agent_search import SearchStatus, SourceCitation, citation_event, search_event
from .agent_types import AgentClientError, AgentStreamEvent, Finish, TextDelta, ToolCallComplete, ToolCallStarted


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
        if self._finish_reason is None:
            out.append(Finish(stop_reason="eof", truncated=True, usage=self._usage, complete=False))
            return out
        reason = self._finish_reason
        out.append(Finish(stop_reason=reason, truncated=reason == "length", usage=self._usage))
        return out


class ResponsesStreamParser:
    """Turns /v1/responses stream events into neutral events."""

    def __init__(self) -> None:
        self._calls: dict[str, dict[str, Any]] = {}
        self._finish: Finish | None = None
        self._citations: set[SourceCitation] = set()
        self._searches: dict[str, SearchStatus] = {}
        self._text_seen: set[tuple[str, int]] = set()

    def _citations_for(self, annotations: Any, item_id: str, content_index: int) -> list[AgentStreamEvent]:
        out: list[AgentStreamEvent] = []
        if not isinstance(annotations, list) or type(content_index) is not int or not 0 <= content_index < 100:
            return out
        for annotation in annotations[:100]:
            citation = citation_event(annotation, item_id, content_index)
            if citation is not None and citation not in self._citations and len(self._citations) < 100:
                self._citations.add(citation)
                out.append(citation)
        return out

    def _item_events(self, item: dict[str, Any]) -> list[AgentStreamEvent]:
        out: list[AgentStreamEvent] = []
        item_id = str(item.get("id") or "")
        if item.get("type") == "web_search_call":
            update = search_event(item)
            if update != self._searches.get(update.call_id):
                self._searches[update.call_id] = update
                out.append(update)
        elif item.get("type") == "message":
            content = item.get("content")
            for index, part in enumerate(content[:100] if isinstance(content, list) else []):
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                if (item_id, index) not in self._text_seen and isinstance(part.get("text"), str):
                    self._text_seen.add((item_id, index))
                    out.append(TextDelta(part["text"], item_id, index))
                out.extend(self._citations_for(part.get("annotations"), item_id, index))
        return out

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
                item_id = str(event.get("item_id") or "")
                index = event.get("content_index", 0)
                index = index if type(index) is int and 0 <= index < 100 else 0
                self._text_seen.add((item_id, index))
                out.append(TextDelta(delta, item_id, index))
        elif event_type == "response.output_text.annotation.added":
            out.extend(self._citations_for([event.get("annotation")], str(event.get("item_id") or ""), event.get("content_index", 0)))
        elif event_type.startswith("response.web_search_call."):
            status = event_type.rsplit(".", 1)[-1]
            if status in {"in_progress", "searching", "completed", "failed"}:
                update = SearchStatus(str(event.get("item_id") or "search")[:200], status)
                if update != self._searches.get(update.call_id):
                    self._searches[update.call_id] = update
                    out.append(update)
        elif event_type == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "web_search_call":
                out.extend(self._item_events(item))
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
            if isinstance(item, dict):
                out.extend(self._item_events(item))
            if isinstance(item, dict) and item.get("type") == "function_call":
                call = self._call_for(item)
                if isinstance(item.get("arguments"), str):
                    call["arguments"] = item["arguments"]
                out.extend(self._complete(call))
        elif event_type in {"response.completed", "response.incomplete"}:
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            output = response.get("output")
            for item in output[:100] if isinstance(output, list) else []:
                if isinstance(item, dict):
                    out.extend(self._item_events(item))
                    if item.get("type") == "function_call":
                        out.extend(self._complete(self._call_for(item)))
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
        out.append(self._finish or Finish(stop_reason="eof", truncated=True, complete=False))
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
    output = data.get("output")
    for item in output[:100] if isinstance(output, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            content = item.get("content")
            for index, part in enumerate(content[:100] if isinstance(content, list) else []):
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    text = part.get("text")
                    if isinstance(text, str) and text:
                        out.append(TextDelta(text, str(item.get("id") or ""), index))
                    annotations = part.get("annotations")
                    for annotation in annotations[:100] if isinstance(annotations, list) else []:
                        citation = citation_event(annotation, str(item.get("id") or ""), index)
                        if citation is not None:
                            out.append(citation)
        elif item.get("type") == "web_search_call":
            out.append(search_event(item))
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
