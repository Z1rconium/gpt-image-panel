import asyncio

import pytest
from fastapi import FastAPI

from backend.app.api.body_limit import BodyLimitMiddleware


@pytest.mark.parametrize("disconnect", [False, True])
def test_json_body_replay_coalesces_tiny_frames_and_preserves_disconnect(disconnect):
    app = FastAPI()

    @app.post("/json")
    def accept(body: dict):
        return body

    received = []
    chunks = iter([*({"type": "http.request", "body": b"x", "more_body": True} for _ in range(10000)),
                   {"type": "http.disconnect"} if disconnect else {"type": "http.request", "body": b"y", "more_body": False}])

    async def receive():
        return next(chunks)

    async def downstream(scope, receive, send):
        message = await receive()
        received.append(message)
        if message.get("more_body"):
            received.append(await receive())

    async def send(message):
        pytest.fail("A bounded request must reach the downstream application")

    scope = {"type": "http", "app": app, "method": "POST", "path": "/json", "root_path": "",
             "headers": [(b"content-type", b"application/json")], "scheme": "http"}
    asyncio.run(BodyLimitMiddleware(downstream)(scope, receive, send))
    assert received[0]["body"] == b"x" * 10000 + (b"" if disconnect else b"y")
    assert len(received) == (2 if disconnect else 1)
    if disconnect:
        assert received[1] == {"type": "http.disconnect"}
