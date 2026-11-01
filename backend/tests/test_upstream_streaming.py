import asyncio
import base64
import json

import pytest

from backend.app.integrations.upstream import generation as upstream_generation
from backend.app.integrations.upstream import transport as upstream_transport
from backend.app.integrations.upstream.errors import UpstreamApiError


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

    async def text(self) -> str:
        return b"".join(self.content._chunks).decode(self.charset)


async def _collect(events_iter):
    return [event async for event in events_iter]


def _sse_bytes(events: list[dict]) -> bytes:
    return b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events)


# ── iter_bounded_sse_json_events ─────────────────────────────────


def test_iter_bounded_sse_json_events_parses_multiple_frames():
    resp = _FakeResponse([b'data: {"a": 1}\n\ndata: {"a": 2}\n\n'])
    events = asyncio.run(
        _collect(
            upstream_transport.iter_bounded_sse_json_events(
                resp, max_total_bytes=1024, max_frame_bytes=1024
            )
        )
    )
    assert events == [{"a": 1}, {"a": 2}]


def test_iter_bounded_sse_json_events_ignores_done_marker():
    resp = _FakeResponse([b'data: {"a": 1}\n\ndata: [DONE]\n\n'])
    events = asyncio.run(
        _collect(
            upstream_transport.iter_bounded_sse_json_events(
                resp, max_total_bytes=1024, max_frame_bytes=1024
            )
        )
    )
    assert events == [{"a": 1}]


def test_iter_bounded_sse_json_events_handles_split_across_chunks():
    frame = b'data: {"partial": true}\n\n'
    resp = _FakeResponse([frame[:10], frame[10:]])
    events = asyncio.run(
        _collect(
            upstream_transport.iter_bounded_sse_json_events(
                resp, max_total_bytes=1024, max_frame_bytes=1024
            )
        )
    )
    assert events == [{"partial": True}]


def test_iter_bounded_sse_json_events_parses_final_frame_without_trailing_blank_line():
    resp = _FakeResponse([b'data: {"a": 1}\n\ndata: {"a": 2}'])
    events = asyncio.run(
        _collect(
            upstream_transport.iter_bounded_sse_json_events(
                resp, max_total_bytes=1024, max_frame_bytes=1024
            )
        )
    )
    assert events == [{"a": 1}, {"a": 2}]


def test_iter_bounded_sse_json_events_raises_on_malformed_json():
    resp = _FakeResponse([b"data: {not json}\n\n"])
    with pytest.raises(UpstreamApiError, match="malformed SSE JSON"):
        asyncio.run(
            _collect(
                upstream_transport.iter_bounded_sse_json_events(
                    resp, max_total_bytes=1024, max_frame_bytes=1024
                )
            )
        )


def test_iter_bounded_sse_json_events_enforces_total_size_cap():
    resp = _FakeResponse([b"data: " + b"x" * 100 + b"\n\n"])
    with pytest.raises(UpstreamApiError, match="exceeded max size"):
        asyncio.run(
            _collect(
                upstream_transport.iter_bounded_sse_json_events(
                    resp, max_total_bytes=10, max_frame_bytes=1024
                )
            )
        )


def test_iter_bounded_sse_json_events_enforces_frame_size_cap():
    resp = _FakeResponse([b"data: " + b"1" * 100 + b"\n\n"])
    with pytest.raises(UpstreamApiError, match="frame exceeded max size"):
        asyncio.run(
            _collect(
                upstream_transport.iter_bounded_sse_json_events(
                    resp, max_total_bytes=10_000, max_frame_bytes=20
                )
            )
        )


# ── consume_streaming_image_response ─────────────────────────────


def test_consume_streaming_image_response_forwards_previews_and_returns_final_usage():
    partial1 = base64.b64encode(b"partial-1").decode()
    partial2 = base64.b64encode(b"partial-2").decode()
    final = base64.b64encode(b"final-image").decode()
    chunks = [
        _sse_bytes(
            [
                {
                    "type": "image_generation.partial_image",
                    "b64_json": partial1,
                    "partial_image_index": 0,
                    "output_format": "png",
                },
                {
                    "type": "image_generation.partial_image",
                    "b64_json": partial2,
                    "partial_image_index": 1,
                    "output_format": "png",
                },
                {
                    "type": "image_generation.completed",
                    "b64_json": final,
                    "usage": {"output_tokens": 1234},
                },
            ]
        )
    ]
    resp = _FakeResponse(chunks)
    previews = []
    data, usage = asyncio.run(
        upstream_generation.consume_streaming_image_response(
            resp,
            "/v1/images/generations",
            None,
            lambda idx, mime, image_bytes: previews.append((idx, mime, image_bytes)),
        )
    )

    assert previews == [(0, "image/png", b"partial-1"), (1, "image/png", b"partial-2")]
    assert data == [{"b64_json": final}]
    assert usage == {"output_tokens": 1234}


def test_consume_streaming_image_response_raises_without_completed_event():
    chunks = [
        _sse_bytes(
            [{"type": "image_generation.partial_image", "b64_json": "AAAA", "partial_image_index": 0}]
        )
    ]
    resp = _FakeResponse(chunks)
    with pytest.raises(UpstreamApiError, match="ended without a completed image event"):
        asyncio.run(
            upstream_generation.consume_streaming_image_response(
                resp, "/v1/images/generations", None, None
            )
        )


def test_consume_streaming_image_response_raises_on_failed_event():
    chunks = [_sse_bytes([{"type": "image_generation.failed", "error": "quota exceeded"}])]
    resp = _FakeResponse(chunks)
    with pytest.raises(UpstreamApiError, match="quota exceeded"):
        asyncio.run(
            upstream_generation.consume_streaming_image_response(
                resp, "/v1/images/generations", None, None
            )
        )


def test_consume_streaming_image_response_rejects_non_event_stream_content_type():
    resp = _FakeResponse([b"{}"], headers={"Content-Type": "application/json"})
    with pytest.raises(UpstreamApiError, match="No image data"):
        asyncio.run(
            upstream_generation.consume_streaming_image_response(
                resp, "/v1/images/generations", None, None
            )
        )


def test_consume_streaming_image_response_surfaces_http_error_status():
    resp = _FakeResponse(
        [b'{"error": {"message": "bad request"}}'],
        status=400,
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(UpstreamApiError, match="bad request"):
        asyncio.run(
            upstream_generation.consume_streaming_image_response(
                resp, "/v1/images/generations", None, None
            )
        )


# ── spool_limited_response / parse_upstream_json_response ────────


def _multibyte_json_body() -> bytes:
    # Echoed non-ASCII prompt pushes a character across the 2048-byte preview cut.
    payload = {"data": [{"revised_prompt": "身" * 751, "url": "https://example.test/a.png"}]}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert body[2047] & 0xC0 == 0x80  # cut lands mid-character
    return body


def test_spool_limited_response_tolerates_preview_cut_mid_character():
    resp = _FakeResponse(
        [_multibyte_json_body()], headers={"Content-Type": "application/json"}
    )
    path, preview, encoding = asyncio.run(
        upstream_transport.spool_limited_response(
            resp, 10 * 1024 * 1024, label="Upstream JSON response"
        )
    )
    try:
        assert encoding == "utf-8"
        assert preview.startswith('{"data": [{"revised_prompt": "身')
        assert path.read_bytes() == _multibyte_json_body()
    finally:
        path.unlink(missing_ok=True)


def test_spool_limited_response_rejects_unknown_charset():
    resp = _FakeResponse(
        [b"{}"], headers={"Content-Type": "application/json"}, charset="no-such-charset"
    )
    with pytest.raises(UpstreamApiError, match="is not valid no-such-charset text"):
        asyncio.run(
            upstream_transport.spool_limited_response(
                resp, 1024, label="Upstream JSON response"
            )
        )


def test_parse_upstream_json_response_handles_multibyte_body_cut_at_preview_boundary():
    resp = _FakeResponse(
        [_multibyte_json_body()], headers={"Content-Type": "application/json"}
    )
    result, _preview = asyncio.run(
        upstream_transport.parse_upstream_json_response(
            resp, "/v1/images/generations", None
        )
    )
    assert result["data"][0]["revised_prompt"] == "身" * 751
