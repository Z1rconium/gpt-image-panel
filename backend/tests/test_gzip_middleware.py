import asyncio
from copy import deepcopy
import gzip

import pytest

from backend.app.api.compression import TextOnlyGZipMiddleware


def _run_response(messages, *, path="/response", accept_encoding="gzip", passthrough=False):
    sent = []

    async def app(scope, receive, send):
        for index, message in enumerate(messages):
            await send(deepcopy(message))
            if passthrough:
                # SSE must reach the client before the producer sends its next
                # event; checking the final body alone would miss buffering.
                assert sent == messages[: index + 1]

    async def receive():
        raise AssertionError("The response producer should not receive requests")

    async def send(message):
        if message["type"] == "http.response.start":
            assert not sent, "Response headers must be sent exactly once"
        else:
            assert sent and sent[0]["type"] == "http.response.start"
        sent.append(deepcopy(message))

    middleware = TextOnlyGZipMiddleware(app, minimum_size=32)
    asyncio.run(
        middleware(
            {
                "type": "http",
                "path": path,
                "headers": [(b"accept-encoding", accept_encoding.encode())],
            },
            receive,
            send,
        )
    )
    return sent


@pytest.mark.parametrize(
    "content_type,status,extra_headers,body",
    [
        (b"text/event-stream; charset=utf-8", 200, [], b"data: event\n\n"),
        (b"text/event-stream", 200, [], b""),
        (b"image/png", 200, [], b"\x89PNG\x00\xff" * 20),
        (b"application/zip", 200, [], b"PK\x00\xff" * 20),
        (b"application/octet-stream", 200, [], b"\x00\xff" * 40),
        (b"", 200, [], b"unknown content" * 10),
        (
            b"text/plain",
            200,
            [(b"content-encoding", b"gzip")],
            gzip.compress(b"already encoded" * 20),
        ),
        (
            b"text/plain",
            206,
            [(b"content-range", b"bytes 0-99/200")],
            b"0123456789" * 10,
        ),
    ],
)
@pytest.mark.parametrize("streaming", [False, True])
def test_gzip_bypass_preserves_headers_and_each_body_chunk(
    content_type, status, extra_headers, body, streaming
):
    headers = [(b"content-type", content_type), *extra_headers]
    if not streaming:
        headers.append((b"content-length", str(len(body)).encode()))
    messages = [{"type": "http.response.start", "status": status, "headers": headers}]
    if streaming:
        messages.append({"type": "http.response.body", "body": body, "more_body": True})
    messages.append({"type": "http.response.body", "body": body, "more_body": False})

    assert _run_response(messages, passthrough=True) == messages


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("content_type", [b"text/plain", b"application/json", b"image/svg+xml"])
def test_gzip_compresses_text_with_one_response_start(content_type, streaming):
    body = b"compressible text " * 100
    messages = [
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", content_type), (b"content-length", str(len(body)).encode())],
        },
        {"type": "http.response.body", "body": body, "more_body": streaming},
    ]
    if streaming:
        messages.append({"type": "http.response.body", "body": b"last chunk", "more_body": False})

    sent = _run_response(messages)
    headers = dict(sent[0]["headers"])
    compressed = b"".join(message["body"] for message in sent[1:])
    assert headers[b"content-encoding"] == b"gzip"
    assert b"Accept-Encoding" in headers[b"vary"]
    assert gzip.decompress(compressed) == body + (b"last chunk" if streaming else b"")
    assert sent[-1]["more_body"] is False
    if streaming:
        assert b"content-length" not in headers
    else:
        assert int(headers[b"content-length"]) == len(compressed)


@pytest.mark.parametrize(
    "path,accept_encoding,body",
    [
        ("/response", "gzip", b"small"),
        ("/response", "gzip", b""),
        ("/response", "identity", b"large text" * 100),
        ("/api/image/example.png", "gzip", b"large text" * 100),
        ("/api/thumb/example.webp", "gzip", b"large text" * 100),
        ("/api/download/example.txt", "gzip", b"large text" * 100),
        ("/api/download-all", "gzip", b"large text" * 100),
    ],
)
def test_gzip_preserves_small_text_and_request_bypasses(path, accept_encoding, body):
    messages = [
        {"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]},
        {"type": "http.response.body", "body": body, "more_body": False},
    ]
    assert _run_response(messages, path=path, accept_encoding=accept_encoding) == messages


@pytest.mark.parametrize("content_type", [b"image/png", b"text/plain"])
def test_gzip_pathsend_preserves_response_order(content_type):
    messages = [
        {"type": "http.response.start", "status": 200, "headers": [(b"content-type", content_type)]},
        {"type": "http.response.pathsend", "path": "/example/file"},
    ]
    assert _run_response(messages) == messages
