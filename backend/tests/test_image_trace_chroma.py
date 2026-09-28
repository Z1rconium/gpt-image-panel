import asyncio
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from backend.app.integrations.upstream import generation, payloads
from backend.app.integrations.upstream.chroma import remove_chroma_background
from backend.app.repositories.gallery.mutations import add_to_gallery_sync
from backend.app.repositories.gallery.queries import get_gallery_entry
from backend.app.schemas.gallery import GalleryEntry
from backend.app.schemas.generation import EditRequest, GenerateJobImage, GenerateRequest
from backend.app.services.job_queue import gallery_entry_job_result
from backend.tests.support.contract import _configure_runtime


def _png(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("mode,color", [
    ("chroma_green", (0, 255, 0)),
    ("chroma_magenta", (255, 0, 255)),
])
def test_chroma_removes_only_edge_connected_backdrop(mode, color):
    image = Image.new("RGB", (32, 32), color)
    ImageDraw.Draw(image).rectangle((8, 8, 23, 23), fill=(210, 20, 20))
    image.putpixel((16, 16), color)
    source = BytesIO()
    image.save(source, format="PNG")

    result = remove_chroma_background(source.getvalue(), mode)
    assert result.status == "applied"
    with Image.open(BytesIO(result.image_bytes)) as processed:
        assert processed.format == "PNG"
        assert processed.getpixel((0, 0))[3] == 0
        assert processed.getpixel((16, 16))[3] == 255
        assert 0 < processed.getpixel((8, 16))[3] < 255


def test_chroma_without_backdrop_preserves_original_bytes():
    source = _png((210, 20, 20))
    result = remove_chroma_background(source, "chroma_green")
    assert result.status == "not_detected"
    assert result.image_bytes == source


def test_chroma_request_sends_opaque_png_and_keeps_original_prompt():
    request = GenerateRequest(prompt="red flower", background="chroma_magenta", output_format="jpeg")
    sent = payloads._build_image_params(request)
    assert request.prompt == "red flower"
    assert sent["background"] == "opaque"
    assert sent["output_format"] == "png"
    assert sent["prompt"].startswith("red flower\n\n")
    assert "#FF00FF" in sent["prompt"]
    with pytest.raises(ValueError, match="only available for image generation"):
        EditRequest(prompt="red flower", background="chroma_green")


def test_response_items_keep_independent_reported_fields():
    result = {"output": [
        {"type": "image_generation_call", "result": "first", "revised_prompt": "first rewrite", "size": "1024x1024"},
        {"type": "image_generation_call", "result": {"b64_json": "second", "quality": "high"}},
    ]}
    assert payloads.extract_response_image_results(result) == [
        {"b64_json": "first", "revised_prompt": "first rewrite", "reported_size": "1024x1024"},
        {"b64_json": "second", "reported_quality": "high"},
    ]


def test_stream_completed_event_keeps_reported_fields():
    from backend.tests.test_upstream_streaming import _FakeResponse, _sse_bytes

    response = _FakeResponse([_sse_bytes([{
        "type": "image_generation.completed", "b64_json": "final",
        "revised_prompt": "rewritten", "size": "1536x1024", "quality": "medium",
    }])])
    data, usage = asyncio.run(generation.consume_streaming_image_response(
        response, "/v1/images/generations", None, None,
    ))
    assert data == [{
        "b64_json": "final", "revised_prompt": "rewritten",
        "reported_size": "1536x1024", "reported_quality": "medium",
    }]
    assert usage is None


def test_gallery_round_trip_and_old_record_defaults(tmp_path):
    _configure_runtime(tmp_path)
    entry = add_to_gallery_sync(
        "trace-1", "original", "auto", "trace-1.png",
        metadata={
            "sent_prompt": "original plus backdrop", "revised_prompt": "rewrite",
            "reported_size": "1024x1024", "reported_quality": "high",
            "upstream_duration_ms": 1250, "chroma_status": "applied",
        },
    )
    loaded = get_gallery_entry(entry.id)
    assert loaded is not None
    assert loaded.sent_prompt == "original plus backdrop"
    assert loaded.revised_prompt == "rewrite"
    assert loaded.reported_size == "1024x1024"
    assert loaded.reported_quality == "high"
    assert loaded.upstream_duration_ms == 1250
    assert loaded.chroma_status == "applied"
    image = GenerateJobImage(**gallery_entry_job_result(loaded))
    assert image.revised_prompt == "rewrite"
    legacy = add_to_gallery_sync("legacy-1", "old", "auto", "legacy-1.png")
    assert get_gallery_entry(legacy.id).reported_size is None


def test_save_maps_each_image_response_to_own_entry(monkeypatch):
    source = _png((210, 20, 20))
    captured = []

    async def extract(_session, image_data, _preview, _max_bytes):
        return source

    async def persist(**kwargs):
        captured.append(kwargs)
        return GalleryEntry(
            id=kwargs["image_id"], prompt=kwargs["prompt"], size=kwargs["size"],
            filename=kwargs["filename"], created_at="2026-01-01T00:00:00Z",
            **kwargs["metadata"],
        )

    monkeypatch.setattr(generation, "extract_image_bytes", extract)
    request = GenerateRequest(prompt="original", n=2)
    entries = asyncio.run(generation.save_gallery_entries_from_upstream_data(
        download_session=None, data=[
            {"b64_json": "first", "revised_prompt": "one", "size": "1024x1024"},
            {"b64_json": "second", "quality": "high"},
        ], response_preview="", payload=request, format_extension="png",
        gallery_metadata={"sent_prompt": "original", "upstream_duration_ms": 85},
        save_message="Saving", progress=None, persist_gallery_entry=persist,
    ))
    assert [entry.revised_prompt for entry in entries] == ["one", None]
    assert [entry.reported_size for entry in entries] == ["1024x1024", None]
    assert [entry.reported_quality for entry in entries] == [None, "high"]
    assert all(item["metadata"]["upstream_duration_ms"] == 85 for item in captured)


def test_save_chroma_marks_final_png_and_fallback(monkeypatch):
    source = BytesIO()
    Image.new("RGB", (32, 32), (0, 255, 0)).save(source, format="PNG")
    raw_images = [source.getvalue(), _png((210, 20, 20))]
    saved = []

    async def extract(_session, image_data, _preview, _max_bytes):
        return raw_images[int(image_data["b64_json"])]

    async def persist(**kwargs):
        saved.append(kwargs)
        return GalleryEntry(
            id=kwargs["image_id"], prompt=kwargs["prompt"], size=kwargs["size"],
            filename=kwargs["filename"], created_at="2026-01-01T00:00:00Z",
            **kwargs["metadata"],
        )

    monkeypatch.setattr(generation, "extract_image_bytes", extract)
    request = GenerateRequest(prompt="subject", n=2, background="chroma_green")
    entries = asyncio.run(generation.save_gallery_entries_from_upstream_data(
        download_session=None, data=[{"b64_json": "0"}, {"b64_json": "1"}],
        response_preview="", payload=request, format_extension="png",
        gallery_metadata={"sent_prompt": payloads.sent_generation_prompt(request)},
        save_message="Saving", progress=None, persist_gallery_entry=persist,
        chroma_mode="chroma_green",
    ))
    assert [entry.chroma_status for entry in entries] == ["applied", "not_detected"]
    assert saved[0]["filename"].endswith(".png")
    assert saved[1]["image_bytes"] == raw_images[1]
    with Image.open(BytesIO(saved[0]["image_bytes"])) as processed:
        assert processed.getpixel((0, 0))[3] == 0
