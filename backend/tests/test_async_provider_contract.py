import asyncio
import json

import pytest

from backend.app.integrations.upstream import async_provider, generation as upstream_client
from backend.app.schemas.generation import GenerateRequest
from backend.tests.support.contract import *  # noqa: F403
from backend.tests.support.contract import _FakePool, _FakeResponse, _wait_for_job

API_URL = "https://queue.example.com"
MODEL = "vendor/image-model"
STATUS_URL = f"{API_URL}/{MODEL}/requests/req-1/status"
RESULT_URL = f"{API_URL}/{MODEL}/requests/req-1"
CANCEL_URL = f"{API_URL}/{MODEL}/requests/req-1/cancel"
IMAGE_URL = "https://cdn.example.com/files/out.png"
QUEUE_CONFIG = {
    "version": 1,
    "auth": {"header": "Authorization", "scheme": "Key"},
    "submit": {
        "path": "/{{model}}",
        "body": {
            "prompt": "{{prompt}}",
            "num_images": "{{n}}",
            "image_size": {"width": "{{width}}", "height": "{{height}}"},
        },
    },
    "poll": {
        "url_path": "$.status_url",
        "status_path": "$.status",
        "done": ["COMPLETED"],
        "failed": ["FAILED", "ERROR"],
        "interval_seconds": 2,
        "timeout_seconds": 600,
    },
    "result": {"url_path": "$.response_url", "images_path": "$.images[*].url", "image_kind": "url"},
    "cancel": {"url_path": "$.cancel_url", "method": "PUT"},
}
SUBMIT = {"request_id": "req-1", "status_url": STATUS_URL, "response_url": RESULT_URL, "cancel_url": CANCEL_URL}


def _json(body, status=200):
    return _FakeResponse(
        status,
        headers={"Content-Type": "application/json"},
        chunks=[json.dumps(body).encode("utf-8")],
    )


class _ScriptedSession:
    """Serves canned responses per (method, url); the last one repeats."""

    def __init__(self, routes):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.calls: list[tuple[str, str, dict]] = []

    def _serve(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        queue = self.routes.get((method, url))
        assert queue, f"unexpected request: {method} {url}"
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def post(self, url, **kwargs):
        return self._serve("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._serve("GET", url, kwargs)

    def put(self, url, **kwargs):
        return self._serve("PUT", url, kwargs)

    def delete(self, url, **kwargs):
        return self._serve("DELETE", url, kwargs)

    def methods(self):
        return [(method, url) for method, url, _ in self.calls]


@pytest.fixture
def fast_provider(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(async_provider, "_sleep", no_sleep)
    # All modules share core.validators, so patching it once covers submit, poll and image download.
    monkeypatch.setattr(async_provider.ssrf, "validate_upstream_url", lambda *a, **k: None)
    monkeypatch.setattr(async_provider.ssrf, "validate_response_peer_ip", lambda *a, **k: None)
    monkeypatch.setattr(async_provider.ssrf, "validate_image_url", lambda *a, **k: None)


def _use_session(monkeypatch, session):
    pool = _FakePool(session)
    monkeypatch.setattr(async_provider, "get_pool", lambda: pool)
    monkeypatch.setattr(upstream_client, "get_pool", lambda: pool)


def _happy_routes(statuses=("IN_QUEUE", "COMPLETED")):
    return {
        ("POST", f"{API_URL}/{MODEL}"): [_json(SUBMIT)],
        ("GET", STATUS_URL): [_json({"status": status}) for status in statuses],
        ("GET", RESULT_URL): [_json({"images": [{"url": IMAGE_URL}]})],
        ("GET", IMAGE_URL): [_FakeResponse(200, chunks=[PNG_BYTES])],  # noqa: F405
    }


def _request(**overrides):
    fields = {"prompt": "a red fox", "model": MODEL, "size": "1024x1024"}
    fields.update(overrides)
    return GenerateRequest(**fields)


def _run(session, monkeypatch, request=None, config=None, key="test-key", **kwargs):
    _use_session(monkeypatch, session)
    return asyncio.run(
        ORIGINAL_CALL_IMAGE_GENERATION_API(  # noqa: F405
            API_URL,
            key,
            "/v1/images/generations",
            request or _request(),
            "queue preset",
            None,
            None,
            provider_config=config or QUEUE_CONFIG,
            persist_gallery_entry=gallery_mutations.add_to_gallery_async,  # noqa: F405
            **kwargs,
        )
    )


def test_submit_poll_fetch_saves_gallery_image(client, monkeypatch, fast_provider):
    session = _ScriptedSession(_happy_routes())
    entries = _run(session, monkeypatch)

    assert len(entries) == 1
    assert session.methods() == [
        ("POST", f"{API_URL}/{MODEL}"),
        ("GET", STATUS_URL),
        ("GET", STATUS_URL),
        ("GET", RESULT_URL),
        ("GET", IMAGE_URL),
    ]
    submit = session.calls[0][2]
    assert submit["json"] == {
        "prompt": "a red fox",
        "num_images": 1,
        "image_size": {"width": 1024, "height": 1024},
    }
    assert submit["headers"]["Authorization"] == "Key test-key"
    assert submit["allow_redirects"] is False
    # The key is only sent to the provider, never to the image host.
    image_headers = session.calls[-1][2]["headers"]
    assert "Authorization" not in image_headers


def test_auto_size_omits_dimensions(client, monkeypatch, fast_provider):
    session = _ScriptedSession(_happy_routes(("COMPLETED",)))
    _run(session, monkeypatch, request=_request(size="auto"))
    assert session.calls[0][2]["json"] == {"prompt": "a red fox", "num_images": 1}


def test_failed_status_raises_without_fetching_result(client, monkeypatch, fast_provider):
    routes = _happy_routes(("IN_PROGRESS", "FAILED"))
    session = _ScriptedSession(routes)
    with pytest.raises(upstream_client.UpstreamApiError, match="failed with status FAILED"):
        _run(session, monkeypatch)
    assert ("GET", RESULT_URL) not in session.methods()


def test_timeout_cancels_remote_task(client, monkeypatch, fast_provider):
    from datetime import datetime, timedelta, timezone

    clock = [0.0]
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def advancing_sleep(_seconds):
        clock[0] += 400.0

    monkeypatch.setattr(async_provider, "_sleep", advancing_sleep)
    monkeypatch.setattr(
        async_provider, "_now", lambda: base + timedelta(seconds=clock[0])
    )
    routes = _happy_routes(("IN_PROGRESS",))
    routes[("PUT", CANCEL_URL)] = [_json({"status": "CANCELLATION_REQUESTED"})]
    session = _ScriptedSession(routes)

    with pytest.raises(upstream_client.UpstreamApiError, match="did not finish within 600"):
        _run(session, monkeypatch)
    assert ("PUT", CANCEL_URL) in session.methods()


def test_cancelled_task_cancels_remote_task(client, monkeypatch, fast_provider):
    async def cancelling_sleep(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(async_provider, "_sleep", cancelling_sleep)
    routes = _happy_routes(("IN_PROGRESS",))
    routes[("PUT", CANCEL_URL)] = [_json({})]
    session = _ScriptedSession(routes)

    with pytest.raises(asyncio.CancelledError):
        _run(session, monkeypatch)
    assert ("PUT", CANCEL_URL) in session.methods()


def test_cross_origin_status_url_is_never_followed(client, monkeypatch, fast_provider):
    evil = {**SUBMIT, "status_url": "https://evil.example/steal"}
    session = _ScriptedSession({("POST", f"{API_URL}/{MODEL}"): [_json(evil)]})
    with pytest.raises(upstream_client.UpstreamApiError, match="same origin"):
        _run(session, monkeypatch)
    assert session.methods() == [("POST", f"{API_URL}/{MODEL}")]


def test_missing_status_url_is_reported(client, monkeypatch, fast_provider):
    session = _ScriptedSession({("POST", f"{API_URL}/{MODEL}"): [_json({"request_id": "x"})]})
    with pytest.raises(upstream_client.UpstreamApiError, match="did not include a status URL"):
        _run(session, monkeypatch)


def test_ssrf_rejection_of_submit_url_is_reported(client, monkeypatch, fast_provider):
    def reject(*_args, **_kwargs):
        raise ValueError("Hostname 'queue.example.com' resolves to private/internal IP(s): '10.0.0.1'")

    monkeypatch.setattr(async_provider.ssrf, "validate_upstream_url", reject)
    session = _ScriptedSession({})
    with pytest.raises(upstream_client.UpstreamApiError, match="Provider URL rejected"):
        _run(session, monkeypatch)
    assert session.calls == []


def test_upstream_error_text_is_redacted(client, monkeypatch, fast_provider):
    body = {"detail": "invalid credentials Bearer abc123verysecrettoken"}
    session = _ScriptedSession({("POST", f"{API_URL}/{MODEL}"): [_json(body, status=401)]})
    with pytest.raises(upstream_client.UpstreamApiError) as error:
        _run(session, monkeypatch)
    assert "abc123verysecrettoken" not in str(error.value)
    assert "401" in str(error.value)


@pytest.mark.parametrize("model", ["../admin", "a//b", "bad model", "x?y=1"])
def test_model_names_cannot_escape_the_url_path(client, monkeypatch, fast_provider, model):
    session = _ScriptedSession({})
    with pytest.raises(upstream_client.UpstreamApiError):
        _run(session, monkeypatch, request=_request(model=model))
    assert session.calls == []


def test_invalid_stored_config_is_a_clear_upstream_error(client, monkeypatch, fast_provider):
    session = _ScriptedSession({})
    with pytest.raises(upstream_client.UpstreamApiError, match="Invalid provider_config"):
        _run(session, monkeypatch, config={"version": 1})
    assert session.calls == []


def test_empty_result_is_an_error(client, monkeypatch, fast_provider):
    routes = _happy_routes(("COMPLETED",))
    routes[("GET", RESULT_URL)] = [_json({"images": []})]
    with pytest.raises(upstream_client.UpstreamApiError, match="did not include any images"):
        _run(_ScriptedSession(routes), monkeypatch)


def test_streaming_is_rejected_and_chroma_now_runs(client, monkeypatch, fast_provider):
    with pytest.raises(upstream_client.UpstreamApiError, match="Streaming"):
        _run(_ScriptedSession({}), monkeypatch, stream=True)
    # Chroma keying is local post-processing, so mapped providers may use it;
    # the upstream receives the opaque background and the keyed prompt suffix.
    session = _ScriptedSession(_happy_routes())
    entries = _run(session, monkeypatch, request=_request(background="chroma_green"))
    assert len(entries) == 1
    submit_body = session.calls[0][2]["json"]
    assert "flat, uniform" in submit_body["prompt"]


def _make_async_preset(client, monkeypatch):
    # A new origin clears the stored key, so bind one through an env reference.
    monkeypatch.setenv("QUEUE_TEST_KEY", "queue-secret")
    settings = client.get("/api/settings").json()
    saved = client.post(
        "/api/settings",
        json={
            "active_preset_id": settings["active_preset_id"],
            "preset_name": "queue",
            "api_url": API_URL,
            "api_key": "${QUEUE_TEST_KEY}",
            "api_path": "/v1/images/generations",
            "default_model": MODEL,
            "provider_kind": "async_json",
            "provider_config": QUEUE_CONFIG,
        },
    )
    assert saved.status_code == 200, saved.text


def test_queue_rejects_unsupported_operations_for_async_presets(client, monkeypatch):
    _make_async_preset(client, monkeypatch)
    streamed = client.post("/api/generate", json={"prompt": "fox", "stream": True})
    assert streamed.status_code == 422
    assert "Streaming preview is not available for async providers" in streamed.text
    # Chroma keying is local, so it is admitted and the job succeeds end to end.
    chroma = client.post(
        "/api/generate", json={"prompt": "fox", "background": "chroma_green"}
    )
    assert chroma.status_code == 202
    assert _wait_for_job(client, chroma.json()["job_id"])["status"] == "success"
    transparent = client.post(
        "/api/generate", json={"prompt": "fox", "background": "transparent"}
    )
    assert transparent.status_code == 422
    assert "transparent background" in transparent.text
    edit = client.post(
        "/api/edits",
        data={"prompt": "fox", "model": MODEL, "size": "auto"},
        files={"image": ("input.png", PNG_BYTES, "image/png")},  # noqa: F405
    )
    assert edit.status_code == 422
    assert "does not declare image edit support" in edit.text


def test_executor_passes_provider_config_only_for_async_presets(client, monkeypatch):
    captured = []
    original = backend_main.proxy.call_image_generation_api  # noqa: F405

    async def capturing(*args, **kwargs):
        captured.append(kwargs.pop("provider_config", "absent"))
        return await original(*args, **kwargs)

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", capturing)  # noqa: F405

    plain = client.post("/api/generate", json={"prompt": "plain fox"})
    assert plain.status_code == 202
    assert _wait_for_job(client, plain.json()["job_id"])["status"] == "success"

    _make_async_preset(client, monkeypatch)
    routed = client.post("/api/generate", json={"prompt": "queued fox"})
    assert routed.status_code == 202
    assert _wait_for_job(client, routed.json()["job_id"])["status"] == "success"

    assert captured[0] == "absent"
    assert captured[1]["poll"]["done"] == ["COMPLETED"]


def test_async_preset_health_validates_mapping_and_probes_submit_path(client, monkeypatch):
    _make_async_preset(client, monkeypatch)
    preset_id = client.get("/api/settings").json()["active_preset_id"]
    probed = {}

    async def fake_probe(api_url, api_path, api_key=""):
        probed.update(api_url=api_url, api_path=api_path)
        return {"status": "ok", "message": "reachable"}

    monkeypatch.setattr(transport_client, "probe_upstream_endpoint", fake_probe)
    monkeypatch.setattr(async_provider.ssrf, "validate_upstream_url", lambda *a, **k: None)

    body = client.post(f"/api/settings/presets/{preset_id}/health").json()
    checks = {check["name"]: check for check in body["checks"]}
    assert checks["provider_config"]["status"] == "ok"
    assert "api_path" not in checks
    assert probed == {"api_url": API_URL, "api_path": f"/{MODEL}"}
    assert body["status"] == "ok"


def test_async_preset_health_reports_broken_stored_mapping(client, monkeypatch):
    _make_async_preset(client, monkeypatch)
    from backend.app.services.presets import get_api_presets

    preset = get_api_presets()[0]
    preset["provider_config"] = {"version": 1}
    preset_id = preset["id"]

    body = client.post(f"/api/settings/presets/{preset_id}/health").json()
    checks = {check["name"]: check for check in body["checks"]}
    assert checks["provider_config"]["status"] == "error"
    assert body["status"] == "error"
