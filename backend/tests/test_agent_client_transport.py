"""agent_client against a real local aiohttp server, so chunked SSE, content
types, status mapping and read timeouts run through real sockets."""

import asyncio
import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from backend.app.integrations import agent_client
from backend.app.integrations.agent_client import (
    AgentClientError,
    AgentTimeoutError,
    AgentToolsUnsupportedError,
    Finish,
    TextDelta,
    ToolCallComplete,
    ToolCallStarted,
    ToolSpec,
    UserItem,
    stream_agent_response,
)
from backend.tests.support.contract import *  # noqa: F403

TOOLS = [ToolSpec("generate_image_batch", "Generate images", {"type": "object", "properties": {}})]


def sse_frame(data) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode()


class LocalUpstream:
    """A tiny OpenAI-compatible endpoint whose answer each test scripts."""

    def __init__(self):
        self.requests: list[dict] = []
        self.handler = None
        self._runner_server: TestServer | None = None
        self.session: aiohttp.ClientSession | None = None

    async def _dispatch(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(
            {"path": request.path, "headers": dict(request.headers), "body": body}
        )
        return await self.handler(request, body)

    async def __aenter__(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._dispatch)
        app.router.add_post("/v1/responses", self._dispatch)
        self._runner_server = TestServer(app)
        await self._runner_server.start_server()
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        await self.session.close()
        await self._runner_server.close()

    def url(self, path: str) -> str:
        return str(self._runner_server.make_url(path))


def wire(monkeypatch, upstream: LocalUpstream, path: str):
    """Point the client at the local server; loopback is fine here, so skip SSRF."""

    async def endpoint(api_url, api_path):
        return upstream.url(path)

    class Pool:
        def get(self, **kwargs):
            return upstream.session

    monkeypatch.setattr(agent_client, "validate_assistant_endpoint_async", endpoint)
    monkeypatch.setattr(agent_client, "get_pool", lambda: Pool())
    monkeypatch.setattr(agent_client.ssrf, "validate_response_peer_ip", lambda *a, **k: None)


async def collect(path: str, **overrides):
    kwargs = dict(
        api_url="https://api.example.com",
        api_key="secret-key",
        api_path=path,
        model="m",
        instructions="sys",
        items=[UserItem("hi")],
        tools=TOOLS,
        timeout_seconds=5,
    )
    kwargs.update(overrides)
    return [event async for event in stream_agent_response(**kwargs)]


async def stream_response(request, frames: list[bytes], *, split_at: int | None = None):
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    payload = b"".join(frames)
    if split_at:
        # Cut mid-frame so the parser has to reassemble across chunks.
        await response.write(payload[:split_at])
        await asyncio.sleep(0.02)
        await response.write(payload[split_at:])
    else:
        for frame in frames:
            await response.write(frame)
    await response.write_eof()
    return response


@pytest.mark.anyio
async def test_chat_stream_over_a_real_socket_with_split_frames(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405
    frames = [
        sse_frame({"choices": [{"delta": {"content": "Hel"}}]}),
        sse_frame({"choices": [{"delta": {"content": "lo"}}]}),
        sse_frame({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "generate_image_batch", "arguments": '{"images"'}}]}}]}),
        sse_frame({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ':[]}'}}]}}]}),
        sse_frame({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]

    async def handler(request, body):
        return await stream_response(request, frames, split_at=len(frames[0]) + 7)

    async with LocalUpstream() as upstream:
        upstream.handler = handler
        wire(monkeypatch, upstream, "/v1/chat/completions")
        events = await collect("/v1/chat/completions")

    assert events[0] == TextDelta("Hel") and events[1] == TextDelta("lo")
    assert events[2] == ToolCallStarted("c1", "generate_image_batch")
    assert events[3] == ToolCallComplete("c1", "generate_image_batch", '{"images":[]}')
    assert isinstance(events[4], Finish) and events[4].stop_reason == "tool_calls"
    sent = upstream.requests[0]
    assert sent["headers"]["Authorization"] == "Bearer secret-key"
    assert sent["body"]["stream"] is True and sent["body"]["tools"][0]["function"]["name"] == "generate_image_batch"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.anyio
async def test_responses_stream_over_a_real_socket(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405
    frames = [
        sse_frame({"type": "response.output_text.delta", "delta": "Working"}),
        sse_frame({"type": "response.output_item.added", "item": {"type": "function_call", "id": "fc1", "call_id": "c9", "name": "continue_generation", "arguments": ""}}),
        sse_frame({"type": "response.function_call_arguments.delta", "item_id": "fc1", "delta": '{"reason":"next"}'}),
        sse_frame({"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc1", "call_id": "c9", "name": "continue_generation", "arguments": '{"reason":"next"}'}}),
        sse_frame({"type": "response.completed", "response": {"status": "completed"}}),
    ]

    async def handler(request, body):
        return await stream_response(request, frames, split_at=len(frames[0]) // 2)

    async with LocalUpstream() as upstream:
        upstream.handler = handler
        wire(monkeypatch, upstream, "/v1/responses")
        events = await collect("/v1/responses")

    assert [type(event) for event in events] == [TextDelta, ToolCallStarted, ToolCallComplete, Finish]
    assert events[2] == ToolCallComplete("c9", "continue_generation", '{"reason":"next"}')
    body = upstream.requests[0]["body"]
    assert body["instructions"] == "sys" and body["input"][0]["role"] == "user" and body["stream"] is True


@pytest.mark.anyio
async def test_gateway_that_ignores_stream_is_read_as_a_json_body(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405

    async def handler(request, body):
        return web.json_response({"choices": [{"message": {"content": "Plain answer"}, "finish_reason": "stop"}]})

    async with LocalUpstream() as upstream:
        upstream.handler = handler
        wire(monkeypatch, upstream, "/v1/chat/completions")
        events = await collect("/v1/chat/completions")
    assert events[0] == TextDelta("Plain answer") and isinstance(events[-1], Finish)


@pytest.mark.anyio
async def test_status_errors_map_to_specific_agent_errors(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405

    async def unsupported(request, body):
        return web.json_response({"error": {"message": "tools are not supported"}}, status=400)

    async def broken(request, body):
        return web.Response(status=500, text="upstream exploded")

    async with LocalUpstream() as upstream:
        wire(monkeypatch, upstream, "/v1/chat/completions")
        upstream.handler = unsupported
        with pytest.raises(AgentToolsUnsupportedError):
            await collect("/v1/chat/completions")
        upstream.handler = unsupported
        # Without tools the same 400 is an ordinary error, not a capability problem.
        with pytest.raises(AgentClientError) as excinfo:
            await collect("/v1/chat/completions", tools=[])
        assert not isinstance(excinfo.value, AgentToolsUnsupportedError) and excinfo.value.status == 400
        upstream.handler = broken
        with pytest.raises(AgentClientError) as excinfo:
            await collect("/v1/chat/completions")
        assert excinfo.value.status == 500


@pytest.mark.anyio
async def test_error_frame_mid_stream_and_read_timeout(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)  # noqa: F405

    async def error_frame(request, body):
        return await stream_response(
            request,
            [sse_frame({"choices": [{"delta": {"content": "part"}}]}), sse_frame({"error": {"message": "context length exceeded"}})],
        )

    async def stalls(request, body):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await asyncio.sleep(5)
        return response

    async with LocalUpstream() as upstream:
        wire(monkeypatch, upstream, "/v1/chat/completions")
        upstream.handler = error_frame
        seen: list = []
        with pytest.raises(AgentClientError, match="context length exceeded"):
            async for event in stream_agent_response(
                api_url="https://api.example.com", api_key="k", api_path="/v1/chat/completions",
                model="m", instructions="s", items=[UserItem("hi")], tools=TOOLS, timeout_seconds=5,
            ):
                seen.append(event)
        assert seen == [TextDelta("part")]

        upstream.handler = stalls
        with pytest.raises(AgentTimeoutError):
            await collect("/v1/chat/completions", timeout_seconds=0.3)
