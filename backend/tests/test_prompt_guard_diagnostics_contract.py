import asyncio
from io import BytesIO

import pytest
from PIL import Image

from backend.app.integrations.upstream import generation, payloads
from backend.app.integrations.upstream.diagnostics import image_diagnostics
from backend.app.repositories.gallery.mutations import add_to_gallery_sync
from backend.app.repositories.gallery.queries import get_gallery_entry
from backend.app.schemas.gallery import GalleryEntry
from backend.app.schemas.generation import GenerateJobImage, GenerateRequest
from backend.app.services.job_queue import gallery_entry_job_result
from backend.tests.support.contract import *  # noqa: F403
from backend.tests.support.contract import _configure_runtime


def _png(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(output, format="PNG")
    return output.getvalue()


def test_prompt_guard_wraps_prompt_for_every_request_shape():
    request = GenerateRequest(prompt="a red fox", model="gpt-image-2")
    guarded = payloads.sent_generation_prompt(request, prompt_guard=True)
    assert guarded == f"{payloads.PROMPT_GUARD_PREFIX}\n\na red fox"
    assert payloads.sent_generation_prompt(request) == "a red fox"

    assert payloads._build_image_params(request, prompt_guard=True)["prompt"] == guarded
    assert payloads._build_image_params(request)["prompt"] == "a red fox"
    assert payloads.build_responses_request_data(request, prompt_guard=True)["prompt"] == guarded
    assert payloads.build_responses_request_data(request)["prompt"] == "a red fox"
    chat = payloads.build_chat_completions_request_data(request, prompt_guard=True)
    assert chat["messages"] == [{"role": "user", "content": guarded}]

    chroma = GenerateRequest(prompt="subject", background="chroma_green")
    chroma_prompt = payloads.sent_generation_prompt(chroma, prompt_guard=True)
    assert chroma_prompt.startswith(f"{payloads.PROMPT_GUARD_PREFIX}\n\nsubject\n\n")
    assert "#00FF00" in chroma_prompt


@pytest.mark.parametrize(
    "request_kwargs,api_path,kwargs,expected",
    [
        ({}, "/v1/images/generations", {"revised_prompt": "A Red   FOX"}, []),
        ({}, "/v1/images/generations", {"revised_prompt": "a fluffy red fox at dawn"}, ["prompt_rewritten"]),
        (
            {},
            "/v1/images/generations",
            {"revised_prompt": "a fluffy red fox at dawn", "prompt_guard": True},
            ["prompt_rewritten_despite_guard"],
        ),
        ({"size": "1024x1536"}, "/v1/images/generations", {"reported_size": "1024x1536", "actual_size": (1024, 1536)}, []),
        ({"size": "1024x1536"}, "/v1/images/generations", {"reported_size": "1024x1024"}, ["size_ignored"]),
        ({"size": "1024x1536"}, "/v1/images/generations", {"actual_size": (1024, 1024)}, ["size_ignored"]),
        ({"size": "auto"}, "/v1/images/generations", {"actual_size": (1024, 1024)}, []),
        ({"quality": "high"}, "/v1/images/generations", {"reported_quality": "low"}, ["quality_ignored"]),
        ({"quality": "high"}, "/v1/images/generations", {"reported_quality": "high"}, []),
        ({"size": "1024x1536"}, "/v1/responses", {"actual_size": (1024, 1024)}, ["params_not_sent"]),
        ({}, "/v1/chat/completions", {}, []),
    ],
)
def test_image_diagnostics_codes(request_kwargs, api_path, kwargs, expected):
    request = GenerateRequest(prompt="a red fox", **request_kwargs)
    prompt_guard = kwargs.pop("prompt_guard", False)
    sent = payloads.sent_generation_prompt(request, prompt_guard=prompt_guard)
    assert image_diagnostics(
        request,
        api_path,
        prompt_guard=prompt_guard,
        sent_prompt=sent,
        **kwargs,
    ) == expected


def test_save_records_diagnostics_from_upstream_output(monkeypatch):
    images = {"square": _png(64, 64)}
    saved = []

    async def extract(_session, image_data, _preview, _max_bytes):
        return images[image_data["b64_json"]]

    async def persist(**kwargs):
        saved.append(kwargs)
        return GalleryEntry(
            id=kwargs["image_id"], prompt=kwargs["prompt"], size=kwargs["size"],
            filename=kwargs["filename"], created_at="2026-01-01T00:00:00Z",
            **kwargs["metadata"],
        )

    monkeypatch.setattr(generation, "extract_image_bytes", extract)
    request = GenerateRequest(prompt="fox", size="1024x1536")
    entries = asyncio.run(generation.save_gallery_entries_from_upstream_data(
        download_session=None,
        data=[{"b64_json": "square", "revised_prompt": "a detailed fox portrait"}],
        response_preview="", payload=request, format_extension="png",
        gallery_metadata={
            "api_path": "/v1/images/generations",
            "sent_prompt": payloads.sent_generation_prompt(request, prompt_guard=True),
        },
        save_message="Saving", progress=None, persist_gallery_entry=persist,
        prompt_guard=True,
    ))
    assert entries[0].diagnostics == ["prompt_rewritten_despite_guard", "size_ignored"]


def test_diagnostics_round_trip_through_gallery_and_job_image(tmp_path):
    _configure_runtime(tmp_path)
    entry = add_to_gallery_sync(
        "diag-1", "fox", "1024x1536", "diag-1.png",
        metadata={"diagnostics": ["prompt_rewritten", "size_ignored"]},
    )
    loaded = get_gallery_entry(entry.id)
    assert loaded is not None
    assert loaded.diagnostics == ["prompt_rewritten", "size_ignored"]
    image = GenerateJobImage(**gallery_entry_job_result(loaded))
    assert image.diagnostics == ["prompt_rewritten", "size_ignored"]
    plain = add_to_gallery_sync("diag-2", "fox", "auto", "diag-2.png")
    assert get_gallery_entry(plain.id).diagnostics == []


def test_preset_prompt_guard_round_trip_and_executor_flag(client, monkeypatch):
    settings = client.get("/api/settings").json()
    assert settings["prompt_guard"] is False
    assert settings["presets"][0]["prompt_guard"] is False

    enabled = client.post(
        "/api/settings",
        json={
            "active_preset_id": settings["active_preset_id"],
            "preset_name": "Primary",
            "api_url": "https://api.example.com",
            "api_path": "/v1/images/generations",
            "prompt_guard": True,
        },
    )
    assert enabled.status_code == 200
    assert enabled.json()["prompt_guard"] is True
    assert client.get("/api/settings").json()["presets"][0]["prompt_guard"] is True

    copied = client.post("/api/settings/presets", json={"name": "Guarded copy"}).json()
    copy = next(preset for preset in copied["presets"] if preset["id"] == copied["active_preset_id"])
    assert copy["prompt_guard"] is True

    captured = {}
    original = backend_main.proxy.call_image_generation_api

    async def capturing_generation_api(*args, **kwargs):
        captured["prompt_guard"] = kwargs.pop("prompt_guard", False)
        return await original(*args, **kwargs)

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", capturing_generation_api)
    resp = client.post("/api/generate", json={"prompt": "guarded fox"})
    assert resp.status_code == 202
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert captured["prompt_guard"] is True
