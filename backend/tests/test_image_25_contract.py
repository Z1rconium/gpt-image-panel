import asyncio
import base64
import io
import json

import pytest
from PIL import Image
from pydantic import ValidationError

from backend.app.core.image_models import MAX_PROMPT_CHARS
from backend.app.integrations.upstream import generation as upstream
from backend.app.schemas.generation import GenerateRequest, validate_image_size
from backend.app.services.assistant_text import _allowed_recommendation
from backend.tests.support.contract import (
    PNG_BYTES,
    _assistant_runtime_payload,
    ORIGINAL_CALL_IMAGE_EDIT_API,
    ORIGINAL_CALL_IMAGE_GENERATION_API,
    _FakePool,
    _FakePostSession,
    _FakeResponse,
    _settings_payload,
    _wait_for_job,
    backend_main,
    gallery_queries,
)

MODELS = [
    "gpt-image-2.5-flare", "gpt-image-2.5-flare-2026-09-08",
    "gpt-image-2.5-sunburst", "gpt-image-2.5-sunburst-2026-09-08",
]


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("quality", ["auto", "low", "medium", "high", "xhigh", "max"])
def test_25_wire_parameters(model, quality):
    request = GenerateRequest(
        prompt="绘制一只猫", model=model, quality=quality, size="1536x864",
        response_format="url", background="transparent", output_format="webp", output_compression=0,
    )
    assert upstream._build_image_params(request) == {
        "model": model, "prompt": "绘制一只猫", "quality": quality, "size": "1536x864",
        "n": 1, "background": "transparent", "output_format": "webp", "output_compression": 0,
    }
    assert request.response_format is None


@pytest.mark.parametrize("response_format", [None, "url", "b64_json"])
def test_25_never_sends_response_format(response_format):
    request = GenerateRequest(prompt="cat", model=MODELS[0], response_format=response_format)
    assert "response_format" not in upstream._build_image_params(request)


@pytest.mark.parametrize("model", ["gpt-image-2", "custom-image-model", "gpt-image-2.5"])
def test_legacy_and_custom_models_preserve_response_format_and_reject_extra_quality(model):
    assert upstream._build_image_params(GenerateRequest(prompt="cat", model=model, response_format="url"))["response_format"] == "url"
    with pytest.raises(ValueError, match="quality"):
        upstream._build_image_params(GenerateRequest(prompt="cat", model=model, quality="max"))


@pytest.mark.parametrize("size", ["auto", "1024x1024", "1536x512", "1280x512", "3840x2160", "2160x3840"])
def test_valid_size_boundaries(size):
    assert validate_image_size(size) == size


@pytest.mark.parametrize("size", ["0x0", "1024x0", "-1024x1024", "1025x1024", "4096x2048", "2048x512", "512x512", "3840x3840", "bad"])
def test_invalid_sizes_return_validation_error(size):
    with pytest.raises(ValidationError):
        GenerateRequest(prompt="cat", size=size)


class RecordingSession(_FakePostSession):
    def __init__(self, response):
        super().__init__(response)
        self.json_payload = None
        self.calls = 0

    def post(self, url, **kwargs):
        self.calls += 1
        self.json_payload = kwargs.get("json")
        return super().post(url, **kwargs)


def install_wire_response(monkeypatch, status=200, body=None):
    if body is None:
        body = {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}
    session = RecordingSession(_FakeResponse(
        status, headers={"Content-Type": "application/json"},
        chunks=[json.dumps(body).encode()], peer_ip="93.184.216.34",
    ))
    monkeypatch.setattr(upstream, "get_pool", lambda: _FakePool(session))
    monkeypatch.setattr(upstream.ssrf, "validate_upstream_url", lambda *args, **kwargs: None)
    monkeypatch.setattr(upstream.ssrf, "validate_response_peer_ip", lambda *args, **kwargs: None)
    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", ORIGINAL_CALL_IMAGE_GENERATION_API)
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", ORIGINAL_CALL_IMAGE_EDIT_API)
    return session


@pytest.mark.parametrize("model", MODELS)
def test_resolved_preset_model_controls_wire_payload_and_history(client, monkeypatch, model):
    session = install_wire_response(monkeypatch)
    settings = client.get("/api/settings").json()
    saved = client.post("/api/settings", json=_settings_payload(settings, default_model=model, default_response_format="url"))
    assert saved.status_code == 200
    prompt = "猫🐈" * 16000
    response = client.post("/api/generate", json={"prompt": prompt, "quality": "max", "n": 2})
    assert response.status_code == 202
    job = _wait_for_job(client, response.json()["job_id"])
    assert job["status"] == "success"
    assert len(job["images"]) == 2
    assert session.calls == 2
    assert session.json_payload["n"] == 1
    assert session.json_payload["model"] == model
    assert session.json_payload["prompt"] == prompt
    assert "response_format" not in session.json_payload
    assert job["model"] == model and job["quality"] == "max" and job["response_format"] is None
    assert gallery_queries.get_gallery_entry(job["image_id"]).prompt == prompt


@pytest.mark.parametrize("model", [MODELS[0], MODELS[2]])
@pytest.mark.parametrize("source_count", [1, 2])
def test_edit_multipart_carries_background_quality_and_long_prompt(client, monkeypatch, model, source_count):
    session = install_wire_response(monkeypatch)
    response = client.post("/api/edits", data={
        "prompt": "猫" * MAX_PROMPT_CHARS, "model": model, "quality": "xhigh",
        "background": "transparent", "output_format": "jpeg", "output_compression": 70,
        "response_format": "b64_json",
    }, files=[("image", (f"source-{i}.png", PNG_BYTES, "image/png")) for i in range(source_count)])
    assert response.status_code == 202
    job = _wait_for_job(client, response.json()["job_id"])
    assert job["status"] == "success"
    fields = {options["name"]: value for options, _headers, value in session.data._fields}
    assert session.requested_url.endswith("/v1/images/edits")
    assert fields["model"] == model
    assert fields["quality"] == "xhigh"
    assert fields["background"] == "transparent"
    assert fields["output_format"] == "png"
    assert fields["prompt"] == "猫" * MAX_PROMPT_CHARS
    assert "response_format" not in fields and "output_compression" not in fields
    assert ("image" if source_count == 1 else "image[]") in fields
    assert job["background"] == "transparent"


@pytest.mark.parametrize("api_path", ["/v1/responses", "/v1/chat/completions"])
def test_25_rejects_wrong_endpoint_before_queueing(client, api_path):
    response = client.post("/api/generate", json={"prompt": "cat", "model": MODELS[0], "api_path": api_path})
    assert response.status_code == 422
    assert "requires" in response.json()["detail"]


def test_edit_rejects_unsupported_format_and_cleans_sources(client):
    from backend.app.core import settings as config
    from pathlib import Path

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buffer, "GIF")
    response = client.post("/api/edits", data={"prompt": "cat", "model": MODELS[0]},
                           files={"image": ("source.gif", buffer.getvalue(), "image/gif")})
    assert response.status_code == 422
    assert "PNG, JPEG or WebP" in response.json()["detail"]
    assert not list((Path(config.DATA_DIR) / "edit-sources").glob("edit-source-*"))


@pytest.mark.parametrize("length", [4001, 32000, 32001])
def test_long_prompt_generation_and_snippets(client, length):
    prompt = "🐈" * length
    expected = 422 if length > MAX_PROMPT_CHARS else 202
    result = client.post("/api/generate", json={"prompt": prompt, "model": MODELS[0]})
    assert result.status_code == expected
    saved = client.post("/api/prompt-snippets", json={"title": "long", "prompt": prompt})
    if length > MAX_PROMPT_CHARS:
        assert saved.status_code == 422
    else:
        assert saved.status_code in {200, 201}
        assert saved.json()["prompt"] == prompt


@pytest.mark.parametrize("status,code", [(403, "model_not_found"), (429, "insufficient_quota"), (429, "rate_limit_exceeded"), (400, "moderation_blocked"), (400, "invalid_parameter")])
def test_upstream_failures_surface_without_automatic_retries(client, monkeypatch, status, code):
    session = install_wire_response(monkeypatch, status, {"error": {"message": code, "code": code}})
    response = client.post("/api/generate", json={"prompt": "cat", "model": MODELS[0]})
    job = _wait_for_job(client, response.json()["job_id"])
    assert job["status"] == "upstream_error"
    assert code in job["error"]
    assert session.calls == 1


def test_assistant_quality_recommendation_uses_result_or_current_model():
    assert _allowed_recommendation("/v1/images/generations", {"quality": "max"}, MODELS[0])["quality"] == "max"
    assert "quality" not in _allowed_recommendation("/v1/images/generations", {"quality": "max", "model_name": "gpt-image-2"}, MODELS[0])


def test_gallery_edit_inherits_25_preset_and_preserves_transparency(client, monkeypatch):
    session = install_wire_response(monkeypatch)
    initial = client.post("/api/generate", json={"prompt": "cat", "model": MODELS[0]})
    initial_job = _wait_for_job(client, initial.json()["job_id"])
    settings = client.get("/api/settings").json()
    assert client.post("/api/settings", json=_settings_payload(settings, default_model=MODELS[2])).status_code == 200
    edited = client.post(f"/api/edits/from-gallery/{initial_job['image_id']}", data={
        "prompt": "keep subject", "quality": "max", "background": "transparent",
    })
    assert edited.status_code == 202
    job = _wait_for_job(client, edited.json()["job_id"])
    assert job["status"] == "success"
    assert job["model"] == MODELS[2]
    entry = gallery_queries.get_gallery_entry(job["image_id"])
    assert entry.background == "transparent"
    assert entry.quality == "max"
    fields = {options["name"]: value for options, _headers, value in session.data._fields}
    assert fields["model"] == MODELS[2]
    assert "response_format" not in fields


def test_25_preview_uses_base64_memory_budget(client, monkeypatch):
    install_wire_response(monkeypatch)
    weights = []

    class Lease:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    def lease(weight):
        weights.append(weight)
        return Lease()

    monkeypatch.setattr(upstream, "upstream_memory_lease", lease)
    result = asyncio.run(upstream.call_image_generation_preview_api(
        "https://api.example.com", "test-key",
        GenerateRequest(prompt="cat", model=MODELS[0], response_format="url"),
    ))
    assert result == PNG_BYTES
    assert weights == [upstream.upstream_task_memory_weight(None)]
    assert weights[0] > upstream.upstream_task_memory_weight("url")


@pytest.mark.parametrize("kind", ["optimizer", "assistant-chat", "assistant-responses"])
def test_assistant_upstream_truncation_is_reported(client, monkeypatch, kind):
    from backend.app.integrations import assistant_client, prompt_optimizer_client

    data = ({"status": "incomplete", "output": []} if kind == "assistant-responses" else {
        "choices": [{"message": {"content": '{"rewritten_prompt":"partial"}'}, "finish_reason": "length"}],
    })
    session = RecordingSession(_FakeResponse(
        200, headers={"Content-Type": "application/json"},
        chunks=[json.dumps(data).encode()], peer_ip="93.184.216.34",
    ))
    module = prompt_optimizer_client if kind == "optimizer" else assistant_client
    monkeypatch.setattr(module, "get_pool", lambda: _FakePool(session))
    if kind == "optimizer":
        with pytest.raises(prompt_optimizer_client.UpstreamOptimizerError, match="truncated"):
            asyncio.run(prompt_optimizer_client.optimize_prompt(
                api_url="https://example.com/v1/chat/completions", api_key="test-key",
                model="assistant", prompt="cat", image_model=MODELS[0], quality="max",
            ))
    else:
        with pytest.raises(assistant_client.AssistantError, match="truncated"):
            asyncio.run(assistant_client.request_assistant_json(
                api_url="https://example.com", api_key="test-key", model="assistant",
                api_path="/v1/responses" if kind == "assistant-responses" else "/v1/chat/completions",
                system_prompt="Improve prompt", user_prompt="cat", schema={"rewritten_prompt": "string"},
            ))


def test_assistant_accepts_32000_characters_with_max_quality(client, monkeypatch):
    from backend.app.services import assistant_text

    settings = client.get("/api/settings").json()
    assert client.post("/api/settings", json=_assistant_runtime_payload(settings)).status_code == 200
    prompt = "猫🐈" * 16000

    async def request(**kwargs):
        assert prompt in kwargs["user_prompt"]
        assert "max" in kwargs["user_prompt"]
        return {"rewritten_prompt": prompt}, "assistant", 1

    monkeypatch.setattr(assistant_text.assistant_client, "request_assistant_json", request)
    response = client.post("/api/assistant/prompt/rewrite", json={"prompt": prompt, "model": MODELS[0], "quality": "max"})
    assert response.status_code == 200
    assert response.json()["rewritten_prompt"] == prompt
