import json

from backend.app.integrations import agent_client
from backend.app.integrations.agent_client import (
    AgentClientError,
    AgentToolsUnsupportedError,
    AssistantTextItem,
    ChatStreamParser,
    Finish,
    ResponsesStreamParser,
    TextDelta,
    ToolCallComplete,
    ToolCallItem,
    ToolCallStarted,
    ToolResultItem,
    ToolSpec,
    UserItem,
    build_chat_payload,
    build_responses_payload,
    parse_complete_chat_response,
    parse_complete_responses_response,
    stream_agent_response,
)
from backend.tests.support.contract import *  # noqa: F403

TOOLS = [ToolSpec("generate_image_batch", "Generate images", {"type": "object", "properties": {}})]
ITEMS = [
    UserItem("draw a fox", images=("data:image/png;base64,AAAA",)),
    AssistantTextItem("Sure."),
    ToolCallItem("call_1", "generate_image_batch", '{"images":[]}'),
    ToolResultItem("call_1", '{"ok":true}'),
    UserItem("thanks"),
]


def _sse(*events, done=True) -> list[bytes]:
    frames = [f"data: {json.dumps(event)}\n\n".encode() for event in events]
    if done:
        frames.append(b"data: [DONE]\n\n")
    return frames


def test_build_chat_payload_groups_tool_calls_and_images():
    payload = build_chat_payload(
        model="m", instructions="sys", items=ITEMS, tools=TOOLS, tool_choice="auto", max_output_tokens=100
    )
    roles = [message["role"] for message in payload["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    user = payload["messages"][1]["content"]
    assert user[1]["image_url"]["url"].startswith("data:image/png")
    assistant = payload["messages"][2]
    assert assistant["content"] == "Sure." and assistant["tool_calls"][0]["function"]["name"] == "generate_image_batch"
    assert payload["messages"][3]["tool_call_id"] == "call_1"
    assert payload["stream"] is True and payload["tool_choice"] == "auto"
    assert payload["tools"][0]["function"]["name"] == "generate_image_batch"
    assert "strict" not in json.dumps(payload)


def test_build_payloads_omit_tools_when_none():
    chat = build_chat_payload(model="m", instructions="s", items=[UserItem("x")], tools=[], tool_choice="none", max_output_tokens=10)
    assert "tools" not in chat and "tool_choice" not in chat
    responses = build_responses_payload(model="m", instructions="s", items=[UserItem("x")], tools=[], tool_choice="none", max_output_tokens=10)
    assert "tools" not in responses


def test_build_responses_payload_shapes_items():
    payload = build_responses_payload(
        model="m", instructions="sys", items=ITEMS, tools=TOOLS, tool_choice="none", max_output_tokens=100
    )
    kinds = [item.get("type") or item.get("role") for item in payload["input"]]
    assert kinds == ["user", "assistant", "function_call", "function_call_output", "user"]
    assert payload["input"][0]["content"][1] == {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}
    assert payload["input"][1]["content"][0]["type"] == "output_text"
    assert payload["tools"][0]["name"] == "generate_image_batch" and payload["tool_choice"] == "none"
    assert payload["instructions"] == "sys"


def test_chat_stream_parser_accumulates_tool_call_deltas():
    parser = ChatStreamParser()
    events = []
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "generate_image_batch", "arguments": '{"ima'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ges":[]}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"total_tokens": 5}},
    ]
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    events.extend(parser.finish())
    assert [type(event) for event in events] == [TextDelta, TextDelta, ToolCallStarted, ToolCallComplete, Finish]
    assert events[3] == ToolCallComplete("c1", "generate_image_batch", '{"images":[]}')
    assert events[4].stop_reason == "tool_calls" and events[4].usage == {"total_tokens": 5}


def test_chat_stream_parser_flags_truncation_and_errors():
    parser = ChatStreamParser()
    parser.feed({"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}]})
    assert parser.finish()[-1].truncated is True
    with pytest.raises(AgentClientError):
        ChatStreamParser().feed({"error": {"message": "boom"}})


def test_responses_stream_parser_handles_function_calls():
    parser = ResponsesStreamParser()
    events = []
    frames = [
        {"type": "response.output_text.delta", "delta": "Hi"},
        {"type": "response.output_item.added", "item": {"type": "function_call", "id": "fc1", "call_id": "c9", "name": "continue_generation", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "item_id": "fc1", "delta": '{"reason":'},
        {"type": "response.function_call_arguments.delta", "item_id": "fc1", "delta": '"next"}'},
        {"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc1", "call_id": "c9", "name": "continue_generation", "arguments": '{"reason":"next"}'}},
        {"type": "response.completed", "response": {"status": "completed", "usage": {"total_tokens": 3}}},
    ]
    for frame in frames:
        events.extend(parser.feed(frame))
    events.extend(parser.finish())
    assert [type(event) for event in events] == [TextDelta, ToolCallStarted, ToolCallComplete, Finish]
    assert events[2] == ToolCallComplete("c9", "continue_generation", '{"reason":"next"}')
    assert events[3].usage == {"total_tokens": 3} and events[3].truncated is False
    with pytest.raises(AgentClientError):
        ResponsesStreamParser().feed({"type": "response.failed", "response": {"status": "failed", "error": {"message": "nope"}}})


def test_complete_response_fallbacks():
    chat = parse_complete_chat_response(
        {
            "choices": [
                {
                    "message": {
                        "content": "Done",
                        "tool_calls": [{"id": "a", "function": {"name": "continue_generation", "arguments": '{"reason":"r"}'}}],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    assert [type(event) for event in chat] == [TextDelta, ToolCallStarted, ToolCallComplete, Finish]
    responses = parse_complete_responses_response(
        {
            "status": "incomplete",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Yo"}]},
                {"type": "function_call", "call_id": "z", "name": "generate_image_batch", "arguments": "{}"},
            ],
        }
    )
    assert [type(event) for event in responses] == [TextDelta, ToolCallStarted, ToolCallComplete, Finish]
    assert responses[-1].truncated is True


def _patch_session(monkeypatch, response):
    session = _FakePostSession(response)  # noqa: F405
    monkeypatch.setattr(agent_client, "get_pool", lambda: _FakePool(session))  # noqa: F405
    return session


async def _collect(**overrides):
    kwargs = dict(
        api_url="https://api.example.com",
        api_key="k",
        api_path="/v1/chat/completions",
        model="m",
        instructions="sys",
        items=[UserItem("hi")],
        tools=TOOLS,
    )
    kwargs.update(overrides)
    return [event async for event in stream_agent_response(**kwargs)]


@pytest.mark.anyio
async def test_stream_agent_response_parses_sse_and_sends_bearer(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405
    response = _FakeResponse(  # noqa: F405
        200,
        headers={"Content-Type": "text/event-stream"},
        chunks=_sse({"choices": [{"delta": {"content": "Hello"}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        peer_ip="93.184.216.34",
    )
    session = _patch_session(monkeypatch, response)
    events = await _collect()
    assert events[0] == TextDelta("Hello") and isinstance(events[-1], Finish)
    assert session.requested_url == "https://api.example.com/v1/chat/completions"
    assert session.headers["Authorization"] == "Bearer k"
    assert session.allow_redirects is False


@pytest.mark.anyio
async def test_stream_agent_response_falls_back_to_json_body(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405
    body = json.dumps({"choices": [{"message": {"content": "Plain"}, "finish_reason": "stop"}]}).encode()
    response = _FakeResponse(200, headers={"Content-Type": "application/json"}, chunks=[body], peer_ip="93.184.216.34")  # noqa: F405
    _patch_session(monkeypatch, response)
    events = await _collect()
    assert events[0] == TextDelta("Plain")


@pytest.mark.anyio
async def test_stream_agent_response_maps_tools_unsupported(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405
    response = _FakeResponse(  # noqa: F405
        400, headers={"Content-Type": "application/json"},
        chunks=[b'{"error":{"message":"tools is not supported for this model"}}'], peer_ip="93.184.216.34",
    )
    _patch_session(monkeypatch, response)
    with pytest.raises(AgentToolsUnsupportedError):
        await _collect()
    response = _FakeResponse(500, headers={}, chunks=[b"oops"], peer_ip="93.184.216.34")  # noqa: F405
    _patch_session(monkeypatch, response)
    with pytest.raises(AgentClientError) as excinfo:
        await _collect()
    assert not isinstance(excinfo.value, AgentToolsUnsupportedError) and excinfo.value.status == 500


@pytest.mark.anyio
async def test_stream_agent_response_rejects_private_peer(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405
    response = _FakeResponse(200, headers={"Content-Type": "text/event-stream"}, chunks=[], peer_ip="10.0.0.5")  # noqa: F405
    _patch_session(monkeypatch, response)
    with pytest.raises(AgentClientError, match="private"):
        await _collect()
