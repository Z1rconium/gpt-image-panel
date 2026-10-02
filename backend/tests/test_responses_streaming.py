import asyncio
import base64
import json

import pytest

from backend.app.integrations.upstream import generation as upstream_generation
from backend.app.integrations.upstream import payloads as upstream_payloads
from backend.app.integrations.upstream.errors import UpstreamApiError
from backend.app.schemas.generation import GenerateRequest


class _FakeContent:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 200,
        headers: dict | None = None,
        charset: str = "utf-8",
    ):
        self.status = status
        self.headers = headers or {"Content-Type": "text/event-stream"}
        self.charset = charset
        self.content = _FakeContent(chunks)


def _sse_bytes(events: list[dict]) -> bytes:
    return b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events)


def _consume(resp, preview=None):
    return asyncio.run(
        upstream_generation.consume_streaming_image_response(resp, "/v1/responses", None, preview)
    )


def test_build_responses_request_data_keeps_prompt_only_shape_without_stream():
    payload = GenerateRequest(prompt="a red cube", model="gpt-image-2")
    data = upstream_payloads.build_responses_request_data(payload)
    assert data == {"prompt": "a red cube", "model": "gpt-image-2"}
    assert "tools" not in data
    assert "stream" not in data


def test_build_responses_request_data_adds_image_generation_tool_when_streaming():
    payload = GenerateRequest(
        prompt="a red cube",
        model="gpt-image-2",
        stream=True,
        partial_images=3,
    )
    data = upstream_payloads.build_responses_request_data(payload, stream=True, partial_images=5)
    assert data["stream"] is True
    assert data["tools"] == [{"type": "image_generation", "partial_images": 3}]


def test_consume_responses_stream_forwards_partial_images_and_final_from_completed():
    partial = base64.b64encode(b"partial-frame").decode()
    final = base64.b64encode(b"final-image").decode()
    events = [
        {
            "type": "response.created",
            "response": {"id": "resp_1", "status": "in_progress"},
        },
        {
            "type": "response.output_text.delta",
            "delta": "Generating your image...",
        },
        {
            "type": "response.image_generation_call.in_progress",
            "item_id": "ig_1",
        },
        {
            "type": "response.image_generation_call.partial_image",
            "item_id": "ig_1",
            "output_index": 1,
            "partial_image_index": 0,
            "partial_image_b64": partial,
            "output_format": "png",
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Here is your image"}],
                    },
                    {
                        "id": "ig_1",
                        "type": "image_generation_call",
                        "status": "completed",
                        "revised_prompt": "A shiny red cube",
                        "result": final,
                    },
                ],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 1408,
                    "total_tokens": 1420,
                },
            },
        },
    ]
    previews = []
    data, usage = _consume(
        _FakeResponse([_sse_bytes(events)]),
        lambda idx, mime, image_bytes: previews.append((idx, mime, image_bytes)),
    )

    assert previews == [(0, "image/png", b"partial-frame")]
    assert data == [
        {
            "b64_json": final,
            "revised_prompt": "A shiny red cube",
        }
    ]
    assert usage == {"input_tokens": 12, "output_tokens": 1408, "total_tokens": 1420}


def test_consume_responses_stream_collects_every_image_generation_call():
    final_a = base64.b64encode(b"final-a").decode()
    final_b = base64.b64encode(b"final-b").decode()
    partial_a = base64.b64encode(b"partial-a").decode()
    partial_b = base64.b64encode(b"partial-b").decode()
    events = [
        {
            "type": "response.image_generation_call.partial_image",
            "output_index": 1,
            "partial_image_index": 0,
            "partial_image_b64": partial_a,
        },
        {
            "type": "response.image_generation_call.partial_image",
            "output_index": 2,
            "partial_image_index": 0,
            "partial_image_b64": partial_b,
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_2",
                "status": "completed",
                "output": [
                    {
                        "id": "ig_a",
                        "type": "image_generation_call",
                        "status": "completed",
                        "size": "1024x1024",
                        "result": final_a,
                    },
                    {
                        "id": "ig_b",
                        "type": "image_generation_call",
                        "status": "completed",
                        "result": final_b,
                    },
                ],
            },
        },
    ]
    previews = []
    data, usage = _consume(
        _FakeResponse([_sse_bytes(events)]),
        lambda idx, mime, image_bytes: previews.append((idx, mime, image_bytes)),
    )

    assert previews == [(0, "image/png", b"partial-a"), (0, "image/png", b"partial-b")]
    assert [item["b64_json"] for item in data] == [final_a, final_b]
    assert data[0]["reported_size"] == "1024x1024"
    assert usage is None


def test_consume_responses_stream_raises_on_response_failed():
    events = [
        {
            "type": "response.failed",
            "response": {
                "id": "resp_3",
                "status": "failed",
                "error": {"code": "moderation_blocked", "message": "generation blocked"},
            },
        },
    ]
    with pytest.raises(UpstreamApiError, match="generation blocked"):
        _consume(_FakeResponse([_sse_bytes(events)]))


def test_consume_responses_stream_raises_on_error_event():
    events = [{"type": "error", "message": "stream unavailable"}]
    with pytest.raises(UpstreamApiError, match="stream unavailable"):
        _consume(_FakeResponse([_sse_bytes(events)]))


def test_consume_responses_stream_raises_clear_error_on_incomplete_without_image():
    events = [
        {
            "type": "response.incomplete",
            "response": {
                "id": "resp_4",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
            },
        },
    ]
    with pytest.raises(UpstreamApiError, match="max_output_tokens"):
        _consume(_FakeResponse([_sse_bytes(events)]))


def test_consume_responses_stream_survives_delayed_partial_after_completed():
    final = base64.b64encode(b"final-image").decode()
    late_partial = base64.b64encode(b"late-partial").decode()
    events = [
        {
            "type": "response.completed",
            "response": {
                "id": "resp_5",
                "status": "completed",
                "output": [
                    {
                        "id": "ig_5",
                        "type": "image_generation_call",
                        "status": "completed",
                        "result": final,
                    }
                ],
                "usage": {"total_tokens": 99},
            },
        },
        {
            "type": "response.image_generation_call.partial_image",
            "partial_image_index": 2,
            "partial_image_b64": late_partial,
        },
    ]
    previews = []
    data, usage = _consume(
        _FakeResponse([_sse_bytes(events)]),
        lambda idx, mime, image_bytes: previews.append((idx, mime, image_bytes)),
    )

    assert [item["b64_json"] for item in data] == [final]
    assert usage == {"total_tokens": 99}
    assert previews == [(2, "image/png", b"late-partial")]


def test_consume_responses_stream_without_completed_event_raises():
    events = [
        {
            "type": "response.image_generation_call.partial_image",
            "partial_image_index": 0,
            "partial_image_b64": base64.b64encode(b"partial").decode(),
        },
    ]
    with pytest.raises(UpstreamApiError, match="ended without a completed image event"):
        _consume(_FakeResponse([_sse_bytes(events)]))
