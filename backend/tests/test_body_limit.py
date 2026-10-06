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


def test_path_limit_matches_whole_segments_only():
    from backend.app.api.body_limit import _max_body_for_path
    from backend.app.core import settings as config

    import_limit = config.IMPORT_ARCHIVE_MAX_MB * 1024 * 1024
    assert _max_body_for_path("/api/import") == import_limit
    assert _max_body_for_path("/api/import/") == import_limit
    assert _max_body_for_path("/api/imports", "application/json") == config.MAX_JSON_BODY_MB * 1024 * 1024
    assert _max_body_for_path("/api/importer") != import_limit


def test_bodyless_methods_skip_route_scan():
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {}

    class ExplodingRoutes:
        def __iter__(self):
            raise AssertionError("GET requests must not scan routes")

    reached = []

    async def downstream(scope, receive, send):
        reached.append(scope["path"])

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pytest.fail("unexpected response")

    class FakeApp:
        routes = ExplodingRoutes()

    scope = {"type": "http", "app": FakeApp(), "method": "GET", "path": "/ping", "root_path": "",
             "headers": [], "scheme": "http"}
    asyncio.run(BodyLimitMiddleware(downstream)(scope, receive, send))
    assert reached == ["/ping"]


def _json_scope(app, headers=()):
    return {"type": "http", "app": app, "method": "POST", "path": "/json", "root_path": "",
            "headers": [(b"content-type", b"application/json"), *headers], "scheme": "http"}


def _json_app():
    app = FastAPI()

    @app.post("/json")
    def accept(body: dict):
        return body

    return app


def test_slow_json_body_times_out_with_408(monkeypatch):
    from backend.app.core import settings as config

    monkeypatch.setattr(config, "ACCESS_KEY", "")
    monkeypatch.setattr(config, "REQUEST_BODY_IDLE_TIMEOUT_SECONDS", 0.05)
    sent = []

    async def receive():
        await asyncio.sleep(5)

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        pytest.fail("a stalled body must not reach the application")

    asyncio.run(BodyLimitMiddleware(downstream)(_json_scope(_json_app()), receive, send))
    assert sent[0]["status"] == 408


def test_unauthenticated_json_body_is_not_buffered(monkeypatch):
    from backend.app.core import settings as config

    monkeypatch.setattr(config, "ACCESS_KEY", "secret-access-key")
    reads = []

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    reached = []

    async def downstream(scope, receive, send):
        reached.append(True)

    async def send(message):
        pytest.fail("the gate must pass the request on without answering itself")

    asyncio.run(BodyLimitMiddleware(downstream)(_json_scope(_json_app()), receive, send))
    assert reached == [True] and reads == []
