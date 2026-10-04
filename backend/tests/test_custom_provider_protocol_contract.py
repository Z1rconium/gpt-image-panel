"""Phase 3 contract: sync/async mapped protocols, edit support, capabilities."""

import asyncio
import base64
import io
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import pytest
from PIL import Image as PILImage

from backend.app.integrations.upstream import async_provider
from backend.app.integrations.upstream.contracts import EditUploads
from backend.app.integrations.upstream.generation import (
    _build_provider_edit_uploads,
    call_image_provider_edit_api,
)
from backend.app.schemas.generation import EditRequest, GenerateRequest
from backend.app.schemas.provider import (
    ProviderCapabilities,
    provider_capabilities,
    resolve_provider_config,
)
from backend.tests.support.contract import (  # noqa: F401
    PNG_BYTES,
    _FakePool,
    _FakeResponse,
    _wait_for_job,
    gallery_mutations,
)

API_URL = "https://mapped.example.com"
MODEL = "vendor/image-model"
TASK_ID = "req-1"
STATUS_URL = f"{API_URL}/jobs/{TASK_ID}"
IMAGE_URL = "https://cdn.example.com/files/out.png"


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
    monkeypatch.setattr(async_provider.ssrf, "validate_upstream_url", lambda *a, **k: None)
    monkeypatch.setattr(async_provider.ssrf, "validate_upstream_url_async", _async_ok)
    monkeypatch.setattr(async_provider.ssrf, "validate_response_peer_ip", lambda *a, **k: None)
    monkeypatch.setattr(async_provider.ssrf, "validate_image_url", lambda *a, **k: None)
    monkeypatch.setattr(async_provider.secrets, "same_origin", lambda *_a, **_k: True)


async def _async_ok(*_args, **_kwargs):
    return None


def _use_session(monkeypatch, session):
    pool = _FakePool(session)
    monkeypatch.setattr(async_provider, "get_pool", lambda: pool)


def _generate_request(**overrides):
    fields = {"prompt": "a red fox", "model": MODEL, "size": "auto"}
    fields.update(overrides)
    return GenerateRequest(**fields)


SYNC_CONFIG = {
    "version": 2,
    "mode": "sync",
    "submit": {"path": "/gen", "body": {"prompt": "{{prompt}}"}},
    "result": {"images_path": "$.data[*].b64_json", "image_kind": "b64_json"},
}

ASYNC_V2_CONFIG = {
    "version": 2,
    "submit": {"path": "/jobs", "body": {"prompt": "{{prompt}}"}},
    "poll": {
        "task_id_path": "$.id",
        "url_template": "/jobs/{{task_id}}",
        "status_path": "$.status",
        "done": ["COMPLETED"],
        "interval_seconds": 0.5,
    },
    "result": {"images_path": "$.images[*].url", "image_kind": "url"},
}


def _run_driver(session, monkeypatch, config, *, payload=None, remote=None, edit=None):
    _use_session(monkeypatch, session)
    return asyncio.run(
        async_provider.run_async_provider(
            api_url=API_URL,
            api_key="test-key",
            provider_config=config,
            payload=payload or _generate_request(),
            progress=None,
            socks5_proxy=None,
            remote=remote,
            edit=edit,
        )
    )


def test_sync_submit_returns_images_without_polling(monkeypatch, fast_provider):
    session = _ScriptedSession(
        {("POST", f"{API_URL}/gen"): [_json({"data": [{"b64_json": "aGk="}]})]}
    )
    items, _preview = _run_driver(session, monkeypatch, SYNC_CONFIG)
    assert len(items) == 1
    assert items[0]["b64_json"] == "aGk="
    assert session.methods() == [("POST", f"{API_URL}/gen")]


def test_sync_submit_unknown_without_idempotency_is_interrupted(monkeypatch, fast_provider):
    session = _ScriptedSession({})
    with pytest.raises(async_provider.UpstreamApiError, match="no idempotency"):
        _run_driver(
            session, monkeypatch, SYNC_CONFIG, remote={"phase": "submitting"}
        )
    assert session.calls == []


def test_sync_submit_unknown_with_idempotency_resubmits_same_key(monkeypatch, fast_provider):
    config = {
        **SYNC_CONFIG,
        "submit": {"path": "/gen", "body": {"prompt": "{{prompt}}"}, "idempotency_header": "X-Key"},
    }
    session = _ScriptedSession(
        {("POST", f"{API_URL}/gen"): [_json({"data": [{"b64_json": "aGk="}]})]}
    )
    _run_driver(
        session,
        monkeypatch,
        config,
        remote={"phase": "submitting", "idempotency_key": "unit-key-1"},
    )
    submit = session.calls[0][2]
    assert submit["headers"]["X-Key"] == "unit-key-1"


def test_get_submit_uses_query_mapping_and_no_body(monkeypatch, fast_provider):
    config = {
        **SYNC_CONFIG,
        "submit": {
            "path": "/gen",
            "method": "GET",
            "query": {"model": "{{model}}", "tag": "v1"},
            "body": {},
        },
    }
    expected_url = f"{API_URL}/gen?model={quote(MODEL, safe='')}&tag=v1"
    session = _ScriptedSession({("GET", expected_url): [_json({"data": [{"b64_json": "aGk="}]})]})
    _run_driver(session, monkeypatch, config)
    assert session.methods() == [("GET", expected_url)]
    assert "json" not in session.calls[0][2]


def test_poll_supports_post_method_and_task_id_query(monkeypatch, fast_provider):
    config = {
        **ASYNC_V2_CONFIG,
        "poll": {
            "task_id_path": "$.id",
            "url_template": "/jobs/{{task_id}}",
            "method": "POST",
            "query": {"id": "{{task_id}}"},
            "status_path": "$.status",
            "done": ["COMPLETED"],
            "interval_seconds": 0.5,
        },
        "result": {"images_path": "$.images[*].url"},
    }
    session = _ScriptedSession(
        {
            ("POST", f"{API_URL}/jobs"): [_json({"id": TASK_ID})],
            ("POST", f"{API_URL}/jobs/{TASK_ID}?id={TASK_ID}"): [
                _json({"status": "COMPLETED", "images": [{"url": IMAGE_URL}]}),
            ],
            ("GET", IMAGE_URL): [_FakeResponse(200, chunks=[PNG_BYTES])],
        }
    )
    _run_driver(session, monkeypatch, config)
    poll_call = session.calls[1]
    assert poll_call[0] == "POST"
    assert poll_call[2]["json"] == {}


def test_missing_task_id_fails_the_unit(monkeypatch, fast_provider):
    session = _ScriptedSession({("POST", f"{API_URL}/jobs"): [_json({"other": "x"})]})
    with pytest.raises(async_provider.UpstreamApiError, match="did not include a task id"):
        _run_driver(session, monkeypatch, ASYNC_V2_CONFIG)


@dataclass
class _Source:
    temp_path: Path
    filename: str = "input.png"
    content_type: str = "image/png"
    byte_size: int = 0
    width: int = 1
    height: int = 1


@pytest.fixture
def edit_files(tmp_path):
    image = tmp_path / "input.png"
    mask = tmp_path / "mask.png"
    image.write_bytes(PNG_BYTES)
    mask.write_bytes(PNG_BYTES)
    return _Source(image, byte_size=len(PNG_BYTES)), _Source(mask, byte_size=len(PNG_BYTES))


JSON_EDIT_CONFIG = {
    **ASYNC_V2_CONFIG,
    "edit_submit": {
        "path": "/edit",
        "method": "POST",
        "body_format": "json",
        "body": {
            "prompt": "{{prompt}}",
            "images": "{{reference_images}}",
            "mask": "{{mask}}",
        },
    },
}


def test_json_edit_inlines_bounded_data_urls(monkeypatch, fast_provider, edit_files):
    image, mask = edit_files
    config = resolve_provider_config(JSON_EDIT_CONFIG)
    uploads = asyncio.run(
        _build_provider_edit_uploads(config.edit_submit, [image], mask)
    )
    session = _ScriptedSession(
        {
            ("POST", f"{API_URL}/edit"): [_json({"id": TASK_ID})],
            ("GET", STATUS_URL): [_json({"status": "COMPLETED", "images": [{"url": IMAGE_URL}]})],
            ("GET", IMAGE_URL): [_FakeResponse(200, chunks=[PNG_BYTES])],
        }
    )
    _run_driver(session, monkeypatch, JSON_EDIT_CONFIG, edit=uploads)
    submit = session.calls[0][2]
    assert submit["json"]["prompt"] == "a red fox"
    assert submit["json"]["images"] == [
        f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"
    ]
    assert submit["json"]["mask"].startswith("data:image/png;base64,")


def test_multipart_edit_streams_temp_files(monkeypatch, fast_provider, edit_files):
    image, mask = edit_files
    config = {
        **ASYNC_V2_CONFIG,
        "edit_submit": {
            "path": "/edit",
            "body_format": "multipart",
            "body": {"prompt": "{{prompt}}"},
            "files": {"images": "image", "mask": "mask"},
        },
    }
    resolved = resolve_provider_config(config)
    uploads = asyncio.run(
        _build_provider_edit_uploads(resolved.edit_submit, [image], mask)
    )
    assert uploads.inline_variables == {}
    assert uploads.parts[0].temp_path == image.temp_path
    assert uploads.mask_part is not None
    session = _ScriptedSession(
        {
            ("POST", f"{API_URL}/edit"): [_json({"id": TASK_ID})],
            ("GET", STATUS_URL): [_json({"status": "COMPLETED", "images": [{"url": IMAGE_URL}]})],
            ("GET", IMAGE_URL): [_FakeResponse(200, chunks=[PNG_BYTES])],
        }
    )
    _run_driver(session, monkeypatch, config, edit=uploads)
    submit = session.calls[0][2]
    assert "json" not in submit
    assert submit["data"] is not None


@pytest.mark.parametrize("failure", [OSError("connection failed"), asyncio.CancelledError()])
def test_multipart_handles_close_before_transport_consumes_files(monkeypatch, edit_files, failure):
    image, mask = edit_files
    resolved = resolve_provider_config({
        **ASYNC_V2_CONFIG,
        "edit_submit": {"path": "/edit", "body_format": "multipart", "body": {},
                        "files": {"images": "image", "mask": "mask"}},
    })
    uploads = asyncio.run(_build_provider_edit_uploads(resolved.edit_submit, [image], mask))
    opened = []
    original_open = type(image.temp_path).open

    def tracked_open(path, *args, **kwargs):
        file = original_open(path, *args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr(type(image.temp_path), "open", tracked_open)
    class BrokenSession:
        def post(self, *args, **kwargs):
            raise failure

    session = BrokenSession()
    with pytest.raises(type(failure)):
        asyncio.run(async_provider._submit_provider_request(
            session, resolved.edit_submit, f"{API_URL}/edit", headers={},
            socks5_proxy=None, body={}, uploads=uploads,
        ))
    assert len(opened) == 2 and all(file.closed for file in opened)


def test_edit_without_edit_submit_section_is_rejected(monkeypatch, fast_provider, edit_files):
    _image, _mask = edit_files
    uploads = EditUploads(parts=(), mask_part=None, inline_variables={})
    session = _ScriptedSession({})
    with pytest.raises(async_provider.UpstreamApiError, match="edit support"):
        _run_driver(session, monkeypatch, ASYNC_V2_CONFIG, edit=uploads)
    assert session.calls == []


def test_provider_edit_api_saves_gallery_entries(monkeypatch, fast_provider, edit_files):
    image, _mask = edit_files
    _use_session(monkeypatch, _ScriptedSession({}))
    entries = []

    async def persist(**kwargs):
        entries.append(kwargs)
        return object()

    request = EditRequest(prompt="edit the fox", model=MODEL, size="auto")

    class _Recorder:
        def __init__(self, routes):
            self.session = _ScriptedSession(routes)
            self.pool = _FakePool(self.session)

    recorder = _Recorder(
        {
            ("POST", f"{API_URL}/edit"): [_json({"id": TASK_ID})],
            ("GET", STATUS_URL): [
                _json({"status": "COMPLETED", "images": [{"url": IMAGE_URL}]})
            ],
            ("GET", IMAGE_URL): [_FakeResponse(200, chunks=[PNG_BYTES])],
        }
    )
    monkeypatch.setattr(async_provider, "get_pool", lambda: recorder.pool)
    monkeypatch.setattr("backend.app.integrations.upstream.generation.get_pool", lambda: recorder.pool)
    asyncio.run(
        call_image_provider_edit_api(
            API_URL,
            "test-key",
            request,
            [image],
            "mapped preset",
            None,
            socks5_proxy=None,
            provider_config=JSON_EDIT_CONFIG,
            persist_gallery_entry=persist,
        )
    )
    assert len(entries) == 1
    submit = recorder.session.calls[0][2]
    assert submit["json"]["prompt"] == "edit the fox"


def test_provider_edit_api_without_mapping_support_is_rejected(monkeypatch, fast_provider, edit_files):
    image, _mask = edit_files
    _use_session(monkeypatch, _ScriptedSession({}))
    with pytest.raises(async_provider.UpstreamApiError, match="edit support"):
        asyncio.run(
            call_image_provider_edit_api(
                API_URL,
                "test-key",
                EditRequest(prompt="x", model=MODEL, size="auto"),
                [image],
                None,
                None,
                socks5_proxy=None,
                provider_config=ASYNC_V2_CONFIG,
                persist_gallery_entry=lambda **_k: None,
            )
        )


# --- Mapping validation and capabilities ----------------------------------- #


def _v2(**overrides):
    base = json.loads(json.dumps(ASYNC_V2_CONFIG))
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "config,fragment",
    [
        (_v2(poll=None), "poll is required in async mode"),
        (_v2(mode="sync", poll=None), None),
        (
            _v2(mode="sync"),
            "poll must be omitted in sync mode",
        ),
        (
            _v2(mode="sync", poll=None, cancel={
                "task_id_path": "$.id",
                "url_template": "/j/{{task_id}}/cancel",
            }),
            "cancel is only available in async mode",
        ),
        (
            _v2(mode="sync", poll=None, result={"url_path": "$.u", "images_path": "$.i"}),
            "sync mode reads images from the submit response",
        ),
        (
            _v2(submit={"path": "/g", "method": "GET", "body": {"p": "{{prompt}}"}}),
            "GET requests cannot carry a body",
        ),
        (
            _v2(submit={"path": "/g", "method": "GET", "body": {}}),
            None,
        ),
        (
            _v2(submit={"path": "/g", "body": {"p": "{{prompt}}"}, "query": {"x": "a b"}}),
            "letters, digits",
        ),
        (
            _v2(poll={
                "task_id_path": "$.id",
                "url_template": "/j/{{task_id}}",
                "method": "POST",
                "query": {"s": "{{prompt}}"},
                "status_path": "$.s",
                "done": ["ok"],
            }),
            "not allowed here",
        ),
        (
            _v2(edit_submit={"body": {"prompt": "{{prompt}}", "imgs": "{{reference_images}}"}}),
            "files.images is required",
        ),
        (
            _v2(edit_submit={
                "body_format": "json",
                "body": {"prompt": "{{prompt}}"},
            }),
            "{{reference_images}}",
        ),
        (
            _v2(edit_submit={
                "body_format": "json",
                "body": {"prompt": "{{prompt}}", "mask": "{{unknown_var}}"},
            }),
            "unknown template variables",
        ),
        (_v2(capabilities={"stream": True}), "stream"),
        (_v2(capabilities={"formats": []}), "must not be empty"),
        (_v2(capabilities={"formats": ["png", "png"]}), "must not repeat"),
    ],
)
def test_mapping_validation_rules(config, fragment):
    from pydantic import ValidationError

    if fragment is None:
        resolve_provider_config(config)
        return
    with pytest.raises(ValidationError, match=fragment.replace("(", "\\(").replace(")", "\\)")):
        resolve_provider_config(config)


def test_capabilities_are_derived_from_the_mapping_shape():
    caps = provider_capabilities(_v2())
    assert caps.edit is False and caps.mask is False
    # {{background}} is absent, so transparent stays conservative.
    assert caps.transparent_background is False

    with_background = _v2(submit={"path": "/g", "body": {"b": "{{background}}"}})
    assert provider_capabilities(with_background).transparent_background is True

    declared = _v2(
        submit={"path": "/g", "body": {"b": "{{background}}"}},
        capabilities={"transparent_background": False, "formats": ["png"]},
    )
    caps = provider_capabilities(declared)
    assert caps.transparent_background is False
    assert caps.formats == ("png",)

    editable = _v2(edit_submit={
        "body": {"prompt": "{{prompt}}", "mask": "{{mask}}", "imgs": "{{reference_images}}"},
        "body_format": "json",
    })
    caps = provider_capabilities(editable)
    assert caps.edit is True
    assert caps.mask is True

    multipart_no_mask = _v2(edit_submit={
        "body": {"prompt": "{{prompt}}"},
        "files": {"images": "image"},
    })
    caps = provider_capabilities(multipart_no_mask)
    assert caps.edit is True
    assert caps.mask is False

    v1 = {
        "version": 1,
        "submit": {"path": "/g", "body": {"prompt": "{{prompt}}"}},
        "poll": {"url_path": "$.u", "status_path": "$.s", "done": ["d"]},
        "result": {"images_path": "$.i"},
    }
    caps = provider_capabilities(v1)
    assert caps.edit is False and caps.mask is False and caps.stream is False
    assert caps.formats == ProviderCapabilities().formats

    # A broken stored mapping must not unlock anything.
    caps = provider_capabilities({"version": 2, "submit": {"path": "/g"}})
    assert caps == ProviderCapabilities()


# --- Queue admission with mapped capabilities ------------------------------- #

EDIT_MAPPING = {
    "version": 2,
    "submit": {"path": "/jobs", "body": {"prompt": "{{prompt}}"}},
    "poll": {
        "task_id_path": "$.id",
        "url_template": "/jobs/{{task_id}}",
        "status_path": "$.status",
        "done": ["COMPLETED"],
    },
    "result": {"images_path": "$.images[*].url", "image_kind": "b64_json"},
    "edit_submit": {
        "path": "/edit",
        "body_format": "multipart",
        "body": {"prompt": "{{prompt}}"},
        "files": {"images": "image", "mask": "mask"},
    },
}


def _make_mapped_preset(client, monkeypatch, mapping):
    monkeypatch.setenv("MAPPED_TEST_KEY", "mapped-secret")
    settings = client.get("/api/settings").json()
    saved = client.post(
        "/api/settings",
        json={
            "active_preset_id": settings["active_preset_id"],
            "preset_name": "mapped",
            "api_url": API_URL,
            "api_key": "${MAPPED_TEST_KEY}",
            "api_path": "/v1/images/generations",
            "default_model": MODEL,
            "provider_kind": "async_json",
            "provider_config": mapping,
        },
    )
    assert saved.status_code == 200, saved.text


def test_settings_response_publishes_capabilities(client, monkeypatch):
    _make_mapped_preset(client, monkeypatch, EDIT_MAPPING)
    settings = client.get("/api/settings").json()
    caps = settings["presets"][0]["provider_capabilities"]
    assert caps["edit"] is True
    assert caps["mask"] is True
    assert caps["stream"] is False
    # The active preset's supports_mask follows the mapping capability.
    assert settings["supports_mask"] is True


def test_mapped_edit_is_admitted_and_routed(client, monkeypatch):
    _make_mapped_preset(client, monkeypatch, EDIT_MAPPING)
    captured = {}

    async def fake_provider_edit(*args, **kwargs):
        captured["mask_source"] = kwargs.get("mask_source")
        captured["provider_config"] = kwargs.get("provider_config")
        captured["image_sources"] = args[3]
        image_id = gallery_mutations.__name__  # unused; keep import referenced
        del image_id
        from backend.app.core import media

        return [
            await gallery_mutations.add_to_gallery_async(
                image_bytes=PNG_BYTES,
                image_id=media.generate_image_id(),
                prompt=args[2].prompt,
                size=args[2].size,
                filename="edit.png",
                metadata={"api_path": "/v1/images/edits"},
            )
        ]

    monkeypatch.setattr(
        "backend.app.main.proxy.call_image_provider_edit_api", fake_provider_edit
    )
    response = client.post(
        "/api/edits",
        data={"prompt": "fox", "model": MODEL, "size": "auto"},
        files={"image": ("input.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 202, response.text
    job = _wait_for_job(client, response.json()["job_id"])
    assert job["status"] == "success"
    assert captured["provider_config"]["edit_submit"]["path"] == "/edit"
    assert captured["mask_source"] is None


def _transparent_mask_png(size: tuple[int, int] = (1, 1)) -> bytes:
    """A palette PNG whose whole canvas is transparent — a valid edit mask."""
    image = PILImage.new("P", size, 0)
    image.putpalette([0, 0, 0] * 256)
    image.info["transparency"] = 0
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_mapped_edit_rejects_mask_when_mapping_cannot_carry_it(client, monkeypatch):
    mapping = json.loads(json.dumps(EDIT_MAPPING))
    mapping["edit_submit"]["files"]["mask"] = ""
    _make_mapped_preset(client, monkeypatch, mapping)
    response = client.post(
        "/api/edits",
        data={"prompt": "fox", "model": MODEL, "size": "auto"},
        files={
            "image": ("input.png", PNG_BYTES, "image/png"),
            "mask": ("mask.png", _transparent_mask_png(), "image/png"),
        },
    )
    assert response.status_code == 422
    assert "cannot send an edit mask" in response.text


def test_mapped_generation_rejects_undeclared_format(client, monkeypatch):
    mapping = json.loads(json.dumps(EDIT_MAPPING))
    mapping["capabilities"] = {"formats": ["png"]}
    _make_mapped_preset(client, monkeypatch, mapping)
    response = client.post(
        "/api/generate",
        json={"prompt": "fox", "model": MODEL, "output_format": "jpeg"},
    )
    assert response.status_code == 422
    assert "supports output formats png" in response.text
