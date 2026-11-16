"""Characterization tests for image job admission and edit staging (W0-3 / C1-S0).

Pins today's HTTP-level status codes, ``detail`` strings, disk/DB cleanup
invariants, ``paste_back`` resolution, persisted ``request_json`` payload shape,
simultaneous-failure precedence, and latent ``role="reference"`` restore behavior.
"""

import io
import json
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image as PILImage

from backend.app.core import settings as config
from backend.app.repositories.db import _connect
from backend.app.repositories.image_jobs import aggregate_image_job_units
from backend.app.services import job_queue
from backend.tests.support.contract import (
    PNG_BYTES,
    _configure_runtime,
    _settings_payload,
    _test_client,
    _wait_for_job,
    backend_main,
    gallery_mutations,
    media,
)
from backend.tests.test_custom_provider_protocol_contract import API_URL, EDIT_MAPPING

FIFTY_MIB = 50 * 1024 * 1024


def _png_bytes(
    size: tuple[int, int] = (8, 8),
    mode: str = "RGB",
    color: tuple[int, ...] = (255, 0, 0),
    fill_alpha: int | None = None,
) -> bytes:
    image = PILImage.new(mode, size, color)
    if fill_alpha is not None:
        image.putalpha(fill_alpha)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _gif_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    image = PILImage.new("RGB", size, (0, 255, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="GIF")
    return buffer.getvalue()


SOURCE_PNG = _png_bytes((8, 8), "RGB", (255, 0, 0))
MASK_PNG = _png_bytes((8, 8), "RGBA", (0, 0, 0, 255), fill_alpha=0)
OPAQUE_PNG = _png_bytes((8, 8), "RGBA", (255, 0, 0, 255), fill_alpha=255)
MARKUP_PNG = _png_bytes((8, 8), "RGBA", (255, 0, 0, 100))
WRONG_SIZE_PNG = _png_bytes((16, 16), "RGBA", (0, 0, 0, 0))


def _install_generic_edit_stub(monkeypatch, captured: list[dict] | None = None):
    async def fake_edit_api(
        api_url,
        api_key,
        payload,
        image_sources,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
        persist_gallery_entry=None,
        mask_source=None,
        mask_coverage=None,
        **kwargs,
    ):
        if captured is not None:
            captured.append(
                {
                    "model": payload.model,
                    "paste_back": payload.paste_back,
                    "mask_present": mask_source is not None,
                    "roles": [source.role for source in image_sources],
                }
            )
        image_id = media.generate_image_id()
        entry = await gallery_mutations.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=f"{image_id}.png",
            metadata={"api_path": "/v1/images/edits", "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)


def _save_active_preset(client, **overrides):
    settings = client.get("/api/settings").json()
    saved = client.post("/api/settings", json=_settings_payload(settings, **overrides))
    assert saved.status_code == 200, saved.text
    return saved.json()


def _create_secondary_preset(client, name: str, **overrides) -> str:
    primary_id = client.get("/api/settings").json()["active_preset_id"]
    created = client.post("/api/settings/presets", json={"name": name, **overrides})
    assert created.status_code == 200, created.text
    alt_id = created.json()["active_preset_id"]
    activated = client.post(f"/api/settings/presets/{primary_id}/activate")
    assert activated.status_code == 200, activated.text
    return alt_id


def _make_async_preset(client, monkeypatch, mapping: dict, *, default_model: str = "vendor/model"):
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
            "default_model": default_model,
            "provider_kind": "async_json",
            "provider_config": mapping,
        },
    )
    assert saved.status_code == 200, saved.text


def _persisted_unit_request(job_id: str) -> tuple[dict, str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT request_json FROM image_job_units WHERE parent_job_id = ? ORDER BY unit_index",
            (job_id,),
        ).fetchall()
    assert len(rows) >= 1
    raw_json = rows[0]["request_json"]
    parsed = json.loads(raw_json)
    aggregate = aggregate_image_job_units(job_id)
    assert aggregate["units"][0]["request"] == parsed
    return parsed, raw_json


def _assert_clean_disk_and_no_jobs(client):
    masks_dir = Path(config.MASKS_DIR)
    edit_sources_dir = Path(config.DATA_DIR) / "edit-sources"
    assert not list(masks_dir.glob("*")) if masks_dir.exists() else True
    assert not list(edit_sources_dir.glob("*")) if edit_sources_dir.exists() else True
    assert job_queue.get_pending_edit_source_bytes() == 0
    assert client.get("/api/generate/jobs?include_finished=true").json() == []


# --------------------------------------------------------------------------- #
# A. Error matrix: preset, model, format, quality, stream, 2.5/2.6 options    #
# --------------------------------------------------------------------------- #


def test_generate_rejects_missing_api_preset_id_exact_detail(client):
    resp = client.post("/api/generate", json={"prompt": "x", "api_preset_id": "missing-id"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "API preset not found"
    _assert_clean_disk_and_no_jobs(client)


@pytest.mark.parametrize("quality", ["xhigh", "max"])
@pytest.mark.parametrize("via_preset", [False, True])
def test_generate_and_edit_reject_unsupported_quality_for_model(client, quality, via_preset):
    if via_preset:
        _save_active_preset(client, default_model="gpt-image-2")
        gen_body = {"prompt": "x", "quality": quality}
        edit_data = {"prompt": "x", "quality": quality}
    else:
        gen_body = {"prompt": "x", "model": "gpt-image-2", "quality": quality}
        edit_data = {"prompt": "x", "model": "gpt-image-2", "quality": quality}

    expected = f"quality '{quality}' is not supported for model 'gpt-image-2'"

    gen_resp = client.post("/api/generate", json=gen_body)
    assert gen_resp.status_code == 422
    assert gen_resp.json()["detail"] == expected

    edit_resp = client.post(
        "/api/edits",
        data=edit_data,
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert edit_resp.status_code == 422
    assert edit_resp.json()["detail"] == expected
    _assert_clean_disk_and_no_jobs(client)


@pytest.mark.parametrize(
    "model",
    [
        "gpt-image-2.5-flare",
        "gpt-image-2.6-flare",
        "gpt-image-2.6-sunburst-2026-11-04",
    ],
)
@pytest.mark.parametrize("api_path", ["/v1/responses", "/v1/chat/completions"])
def test_generate_rejects_25_and_26_on_non_images_api_path(client, model, api_path):
    resp = client.post(
        "/api/generate",
        json={"prompt": "x", "model": model, "api_path": api_path},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "GPT Image 2.5/2.6 requires /v1/images/generations or /v1/images/edits"

    _save_active_preset(client, api_path=api_path, default_model=model)
    preset_resp = client.post("/api/generate", json={"prompt": "x"})
    assert preset_resp.status_code == 422
    assert preset_resp.json()["detail"] == "GPT Image 2.5/2.6 requires /v1/images/generations or /v1/images/edits"
    _assert_clean_disk_and_no_jobs(client)


@pytest.mark.parametrize("model", ["gpt-image-2", "gpt-image-2.5-flare"])
def test_generate_and_edit_reject_complexity_on_non_26_model(client, model):
    gen_resp = client.post(
        "/api/generate",
        json={"prompt": "x", "model": model, "complexity": "low"},
    )
    assert gen_resp.status_code == 422
    assert gen_resp.json()["detail"] == "complexity requires GPT Image 2.6"

    edit_resp = client.post(
        "/api/edits",
        data={"prompt": "x", "model": model, "complexity": "low"},
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert edit_resp.status_code == 422
    assert edit_resp.json()["detail"] == "complexity requires GPT Image 2.6"
    _assert_clean_disk_and_no_jobs(client)


@pytest.mark.parametrize("model", ["gpt-image-2", "gpt-image-2.5-flare"])
@pytest.mark.parametrize("output_format", ["svg", "gif"])
def test_generate_rejects_svg_and_gif_on_non_26_model(client, model, output_format):
    resp = client.post(
        "/api/generate",
        json={"prompt": "x", "model": model, "output_format": output_format},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "SVG/GIF requires GPT Image 2.6"
    _assert_clean_disk_and_no_jobs(client)


@pytest.mark.parametrize(
    "payload, expected_detail",
    [
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "svg", "n": 2},
            "SVG requires n=1",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "svg", "stream": True},
            "SVG/GIF does not support streaming, compression or chroma backgrounds",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif", "stream": True},
            "SVG/GIF does not support streaming, compression or chroma backgrounds",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "svg", "output_compression": 90},
            "SVG/GIF does not support streaming, compression or chroma backgrounds",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif", "output_compression": 90},
            "SVG/GIF does not support streaming, compression or chroma backgrounds",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "svg", "background": "chroma_green"},
            "SVG/GIF does not support streaming, compression or chroma backgrounds",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif", "background": "chroma_magenta"},
            "SVG/GIF does not support streaming, compression or chroma backgrounds",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif", "frame_duration_ms": 25},
            "frame_duration_ms must be a multiple of 10",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "png", "frame_count": 9},
            "frame fields are GIF-only",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "png", "frame_duration_ms": 200},
            "frame fields are GIF-only",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "svg", "partial_images": 2},
            "SVG/GIF does not support partial_images",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif", "partial_images": 2},
            "SVG/GIF does not support partial_images",
        ),
    ],
)
def test_generate_rejects_invalid_format_and_control_combinations(client, payload, expected_detail):
    resp = client.post("/api/generate", json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"] == expected_detail
    _assert_clean_disk_and_no_jobs(client)


@pytest.mark.parametrize(
    "data, expected_fragment",
    [
        ({"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "svg"}, "SVG is generation-only"),
        (
            {"prompt": "x", "model": "gpt-image-2", "background": "chroma_green"},
            "Local chroma removal is only available for image generation",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2", "background": "chroma_magenta"},
            "Local chroma removal is only available for image generation",
        ),
    ],
)
def test_edit_rejects_generation_only_format_and_chroma(client, data, expected_fragment):
    resp = client.post(
        "/api/edits",
        data=data,
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert resp.status_code == 422
    assert expected_fragment in resp.json()["detail"]
    _assert_clean_disk_and_no_jobs(client)


def test_generate_rejects_stream_on_chat_completions_exact_detail(client):
    resp = client.post(
        "/api/generate",
        json={"prompt": "x", "model": "gpt-image-2", "stream": True, "api_path": "/v1/chat/completions"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        "Streaming preview requires /v1/images/generations, /v1/images/edits or /v1/responses"
    )

    _save_active_preset(client, api_path="/v1/chat/completions")
    preset_resp = client.post("/api/generate", json={"prompt": "x", "model": "gpt-image-2", "stream": True})
    assert preset_resp.status_code == 422
    assert preset_resp.json()["detail"] == (
        "Streaming preview requires /v1/images/generations, /v1/images/edits or /v1/responses"
    )
    _assert_clean_disk_and_no_jobs(client)


def test_generate_rejects_missing_api_url_exact_detail(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    monkeypatch.setattr(config, "DEFAULT_API_URL", "")
    with _test_client() as unconfigured_client:
        resp = unconfigured_client.post("/api/generate", json={"prompt": "x"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "API URL not configured. Please set it in Settings."
        _assert_clean_disk_and_no_jobs(unconfigured_client)


# --------------------------------------------------------------------------- #
# B. Error matrix: async-provider capability checks                           #
# --------------------------------------------------------------------------- #


def test_async_provider_rejects_complexity_absent_from_operation_template(client, monkeypatch):
    # Generation lacks {{complexity}} even though edit_submit has it.
    gen_missing = deepcopy(EDIT_MAPPING)
    gen_missing["edit_submit"]["body"]["complexity"] = "{{complexity}}"
    _make_async_preset(client, monkeypatch, gen_missing, default_model="gpt-image-2.6-flare")
    gen_resp = client.post(
        "/api/generate",
        json={"prompt": "x", "model": "gpt-image-2.6-flare", "complexity": "low"},
    )
    assert gen_resp.status_code == 422
    assert gen_resp.json()["detail"] == "Provider mapping does not send complexity for this operation"

    # Edit lacks {{complexity}} even though submit has it.
    edit_missing = deepcopy(EDIT_MAPPING)
    edit_missing["submit"]["body"]["complexity"] = "{{complexity}}"
    _make_async_preset(client, monkeypatch, edit_missing, default_model="gpt-image-2.6-flare")
    edit_resp = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2.6-flare", "complexity": "low"},
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert edit_resp.status_code == 422
    assert edit_resp.json()["detail"] == "Provider mapping does not send complexity for this operation"
    _assert_clean_disk_and_no_jobs(client)


def test_async_provider_rejects_gif_when_animation_controls_missing_from_operation(client, monkeypatch):
    mapping = deepcopy(EDIT_MAPPING)
    mapping["capabilities"] = {"formats": ["png", "gif"]}
    _make_async_preset(client, monkeypatch, mapping, default_model="gpt-image-2.6-flare")

    gen_resp = client.post(
        "/api/generate",
        json={"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif"},
    )
    assert gen_resp.status_code == 422
    assert gen_resp.json()["detail"] == "Provider mapping does not send animation controls for this operation"

    # Submit has animation controls, edit_submit does not.
    mapping["submit"]["body"].update(frame_count="{{frame_count}}", frame_duration_ms="{{frame_duration_ms}}")
    _make_async_preset(client, monkeypatch, mapping, default_model="gpt-image-2.6-flare")
    edit_resp = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif"},
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert edit_resp.status_code == 422
    assert edit_resp.json()["detail"] == "Provider mapping does not send animation controls for this operation"
    _assert_clean_disk_and_no_jobs(client)


def test_async_provider_rejects_markup_without_markup_capability_and_cleans_up(client, monkeypatch):
    _make_async_preset(client, monkeypatch, deepcopy(EDIT_MAPPING), default_model="gpt-image-2.6-flare")
    resp = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2.6-flare"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "markup": ("markup.png", MARKUP_PNG, "image/png"),
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Provider mapping does not send markup"
    _assert_clean_disk_and_no_jobs(client)


def test_async_provider_rejects_other_missing_capabilities_exact_detail(client, monkeypatch):
    no_edit = deepcopy(EDIT_MAPPING)
    no_edit.pop("edit_submit")
    _make_async_preset(client, monkeypatch, no_edit)

    assert client.post(
        "/api/generate", json={"prompt": "x", "stream": True}
    ).json()["detail"] == "Streaming preview is not available for async providers"

    assert client.post(
        "/api/generate", json={"prompt": "x", "background": "transparent"}
    ).json()["detail"] == (
        "This provider mapping does not declare native transparent background "
        "support; pick a chroma background or declare "
        "capabilities.transparent_background in provider_config"
    )

    assert client.post(
        "/api/edits",
        data={"prompt": "x"},
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    ).json()["detail"] == (
        "This provider mapping does not declare image edit support; "
        "add an edit_submit section to provider_config"
    )

    no_mask = deepcopy(EDIT_MAPPING)
    no_mask["edit_submit"]["files"]["mask"] = ""
    _make_async_preset(client, monkeypatch, no_mask)
    mask_resp = client.post(
        "/api/edits",
        data={"prompt": "x"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert mask_resp.status_code == 422
    assert mask_resp.json()["detail"] == (
        "This provider mapping cannot send an edit mask; configure "
        "edit_submit.files.mask (multipart) or {{mask}} in a JSON body"
    )

    png_only = deepcopy(EDIT_MAPPING)
    png_only["capabilities"] = {"formats": ["png"]}
    _make_async_preset(client, monkeypatch, png_only)
    fmt_resp = client.post("/api/generate", json={"prompt": "x", "output_format": "jpeg"})
    assert fmt_resp.status_code == 422
    assert fmt_resp.json()["detail"] == "This provider mapping supports output formats png; 'jpeg' was requested"
    _assert_clean_disk_and_no_jobs(client)


# --------------------------------------------------------------------------- #
# C. Error matrix: markup/mask/upload limits, 50 MiB, body limit, restore     #
# --------------------------------------------------------------------------- #


def test_edit_rejects_mask_and_markup_combined(client):
    resp = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2.6-flare"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
            "markup": ("markup.png", MARKUP_PNG, "image/png"),
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Mask and markup cannot be combined"
    _assert_clean_disk_and_no_jobs(client)


@pytest.mark.parametrize("model", ["", "gpt-image-2", "gpt-image-2.5-flare"])
def test_edit_rejects_markup_on_non_26_model_http(client, model):
    data = {"prompt": "x"}
    if model:
        data["model"] = model
    resp = client.post(
        "/api/edits",
        data=data,
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "markup": ("markup.png", MARKUP_PNG, "image/png"),
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Markup requires GPT Image 2.6"
    _assert_clean_disk_and_no_jobs(client)


def test_edit_markup_validation_errors_http(client):
    base_data = {"prompt": "x", "model": "gpt-image-2.6-flare"}
    primary_file = ("image", ("input.png", SOURCE_PNG, "image/png"))

    empty = client.post(
        "/api/edits",
        data=base_data,
        files=[primary_file, ("markup", ("markup.png", b"", "image/png"))],
    )
    assert empty.status_code == 400
    assert empty.json()["detail"] == "Markup is empty"

    bad_header = client.post(
        "/api/edits",
        data=base_data,
        files=[primary_file, ("markup", ("markup.png", b"not-a-png", "image/png"))],
    )
    assert bad_header.status_code == 400
    assert bad_header.json()["detail"] == "Image data must be a supported raster image format"

    wrong_size = client.post(
        "/api/edits",
        data=base_data,
        files=[primary_file, ("markup", ("markup.png", WRONG_SIZE_PNG, "image/png"))],
    )
    assert wrong_size.status_code == 422
    assert wrong_size.json()["detail"] == "Markup dimensions must match the oriented primary image"

    opaque = client.post(
        "/api/edits",
        data=base_data,
        files=[primary_file, ("markup", ("markup.png", OPAQUE_PNG, "image/png"))],
    )
    assert opaque.status_code == 422
    assert opaque.json()["detail"] == "Markup must contain transparent pixels"

    multi_markup = client.post(
        "/api/edits",
        data=base_data,
        files=[
            primary_file,
            ("markup", ("m1.png", MARKUP_PNG, "image/png")),
            ("markup", ("m2.png", MARKUP_PNG, "image/png")),
        ],
    )
    assert multi_markup.status_code == 422
    assert multi_markup.json()["detail"] == "Upload one PNG markup layer"

    huge_4097 = _png_bytes((4097, 4097), "RGB", (255, 0, 0))
    huge_markup = _png_bytes((4097, 4097), "RGBA", (0, 0, 0, 0))
    over_pixels = client.post(
        "/api/edits",
        data=base_data,
        files=[
            ("image", ("huge.png", huge_4097, "image/png")),
            ("markup", ("markup.png", huge_markup, "image/png")),
        ],
    )
    assert over_pixels.status_code == 422
    assert over_pixels.json()["detail"] == "Markup primary must contain at most 16777216 pixels"
    _assert_clean_disk_and_no_jobs(client)


def test_edit_rejects_gif_output_with_mask_exact_detail(client):
    resp = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "GIF output does not support edit masks"
    _assert_clean_disk_and_no_jobs(client)


def test_edit_50_mib_boundary_for_image_and_markup(client, monkeypatch):
    _install_generic_edit_stub(monkeypatch)
    base_png = _png_bytes((1, 1), "RGB", (255, 0, 0))
    base_markup = _png_bytes((1, 1), "RGBA", (255, 0, 0, 100))

    below_limit_img = base_png + (b"\x00" * (FIFTY_MIB - 1 - len(base_png)))
    at_limit_img = base_png + (b"\x00" * (FIFTY_MIB - len(base_png)))
    above_limit_img = base_png + (b"\x00" * (FIFTY_MIB + 1 - len(base_png)))

    ok_img = client.post(
        "/api/edits",
        data={"prompt": "ok 50MiB-1", "model": "gpt-image-2.6-flare"},
        files={"image": ("ok.png", below_limit_img, "image/png")},
    )
    assert ok_img.status_code == 202, ok_img.text
    assert _wait_for_job(client, ok_img.json()["job_id"])["status"] == "success"

    for model in ("gpt-image-2.5-flare", "gpt-image-2.6-flare"):
        exact_50 = client.post(
            "/api/edits",
            data={"prompt": "exact 50MiB", "model": model},
            files={"image": ("exact.png", at_limit_img, "image/png")},
        )
        assert exact_50.status_code == 422
        assert exact_50.json()["detail"] == "GPT Image 2.5/2.6 edit inputs must be smaller than 50 MB"

    over_50 = client.post(
        "/api/edits",
        data={"prompt": "over 50MiB", "model": "gpt-image-2.6-flare"},
        files={"image": ("over.png", above_limit_img, "image/png")},
    )
    assert over_50.status_code == 400
    assert over_50.json()["detail"] == "Uploaded image is too large. Max size is 50 MB."

    below_limit_markup = base_markup + (b"\x00" * (FIFTY_MIB - 1 - len(base_markup)))
    at_limit_markup = base_markup + (b"\x00" * (FIFTY_MIB - len(base_markup)))

    ok_markup = client.post(
        "/api/edits",
        data={"prompt": "ok markup 50MiB-1", "model": "gpt-image-2.6-flare"},
        files={
            "image": ("small.png", base_png, "image/png"),
            "markup": ("markup.png", below_limit_markup, "image/png"),
        },
    )
    assert ok_markup.status_code == 202, ok_markup.text
    assert _wait_for_job(client, ok_markup.json()["job_id"])["status"] == "success"

    bad_markup = client.post(
        "/api/edits",
        data={"prompt": "exact markup 50MiB", "model": "gpt-image-2.6-flare"},
        files={
            "image": ("small.png", base_png, "image/png"),
            "markup": ("markup.png", at_limit_markup, "image/png"),
        },
    )
    assert bad_markup.status_code == 400
    assert bad_markup.json()["detail"] == "Markup must be smaller than 50 MiB"

    edit_sources_dir = Path(config.DATA_DIR) / "edit-sources"
    assert not list(edit_sources_dir.glob("*")) if edit_sources_dir.exists() else True
    assert job_queue.get_pending_edit_source_bytes() == 0


def test_body_limit_rejects_oversized_generate_json_and_edit_multipart(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_JSON_BODY_MB", 1)
    big_json = client.post(
        "/api/generate",
        content=b'{"prompt": "' + (b"a" * (1024 * 1024 + 64)) + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert big_json.status_code == 413
    assert big_json.json()["detail"] == "Request body too large"

    monkeypatch.setattr(config, "MAX_FILE_SIZE_MB", 1)
    # /api/edits body cap becomes 16*1 + 4 + 1 + 1 = 22 MiB.
    oversized_body = b"x" * (23 * 1024 * 1024)
    big_edit = client.post(
        "/api/edits",
        data={"prompt": "too big"},
        files={"image": ("big.png", oversized_body, "image/png")},
    )
    assert big_edit.status_code == 413
    assert big_edit.json()["detail"] == "Request body too large"
    _assert_clean_disk_and_no_jobs(client)


def test_missing_markup_and_invalid_restore_identity_errors(client, monkeypatch):
    _install_generic_edit_stub(monkeypatch)
    created = client.post(
        "/api/edits",
        data={"prompt": "with mask", "model": "gpt-image-2"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert created.status_code == 202
    masked_job_id = created.json()["job_id"]
    _wait_for_job(client, masked_job_id)

    # Missing markup on unknown job and on masked-only job -> 404.
    for target_job in ("missing-job-id", masked_job_id):
        get_resp = client.get(f"/api/generate/{target_job}/markup")
        assert get_resp.status_code == 404
        assert get_resp.json()["detail"] == "No markup is stored for this job"

        restore_resp = client.post(
            f"/api/generate/{target_job}/markup/restore",
            data={"source_sha256": "0" * 64},
        )
        assert restore_resp.status_code == 404
        assert restore_resp.json()["detail"] == "No markup is stored for this job"

    # Missing or ambiguous restore identity on mask/restore -> 400.
    neither = client.post(f"/api/generate/{masked_job_id}/mask/restore", data={})
    assert neither.status_code == 400
    assert neither.json()["detail"] == "Provide exactly one edit source identity"

    both = client.post(
        f"/api/generate/{masked_job_id}/mask/restore",
        data={"source_sha256": "0" * 64, "gallery_image_id": "img-1"},
    )
    assert both.status_code == 400
    assert both.json()["detail"] == "Provide exactly one edit source identity"

    # Same 400 check on markup/restore.
    marked = client.post(
        "/api/edits",
        data={"prompt": "with markup", "model": "gpt-image-2.6-flare"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "markup": ("markup.png", MARKUP_PNG, "image/png"),
        },
    )
    assert marked.status_code == 202
    markup_job_id = marked.json()["job_id"]
    _wait_for_job(client, markup_job_id)

    neither_markup = client.post(f"/api/generate/{markup_job_id}/markup/restore", data={})
    assert neither_markup.status_code == 400
    assert neither_markup.json()["detail"] == "Provide exactly one edit source identity"

    both_markup = client.post(
        f"/api/generate/{markup_job_id}/markup/restore",
        data={"source_sha256": "0" * 64, "gallery_image_id": "img-1"},
    )
    assert both_markup.status_code == 400
    assert both_markup.json()["detail"] == "Provide exactly one edit source identity"


# --------------------------------------------------------------------------- #
# D. No leaked {job_id}.png / {job_id}-markup.png or temp sources on 422/400   #
# --------------------------------------------------------------------------- #


def test_post_staging_422_and_400_leave_no_layer_files_or_temp_sources(client, monkeypatch):
    # 1. Valid mask + valid GIF primary on 2.6 -> fails in queue_image_job (422)
    #    AFTER queue_edit_job wrote {job_id}.png; cleanup must remove it.
    gif_primary = _gif_bytes((8, 8))
    gif_with_mask = client.post(
        "/api/edits",
        data={"prompt": "gif primary", "model": "gpt-image-2.6-flare"},
        files={
            "image": ("primary.gif", gif_primary, "image/gif"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert gif_with_mask.status_code == 422
    assert gif_with_mask.json()["detail"] == (
        "GPT Image 2.5/2.6 edit inputs must be PNG, JPEG or WebP; convert this image before editing"
    )
    _assert_clean_disk_and_no_jobs(client)

    # 2. Malformed image upload -> 400, no leaked source.
    malformed = client.post(
        "/api/edits",
        data={"prompt": "broken"},
        files={"image": ("broken.png", b"\x89PNG\r\n\x1a\ntruncated", "image/png")},
    )
    assert malformed.status_code == 400
    assert malformed.json()["detail"] == "Image data must be a fully decodable supported raster image"
    _assert_clean_disk_and_no_jobs(client)

    # 3. Valid mask + async_json provider without mask capability -> fails after
    #    {job_id}.png copy; cleanup must remove {job_id}.png and temp sources.
    no_mask_mapping = deepcopy(EDIT_MAPPING)
    no_mask_mapping["edit_submit"]["files"]["mask"] = ""
    _make_async_preset(client, monkeypatch, no_mask_mapping)
    no_mask_resp = client.post(
        "/api/edits",
        data={"prompt": "no mask cap"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert no_mask_resp.status_code == 422
    _assert_clean_disk_and_no_jobs(client)

    # 4. Valid markup + async_json provider without markup capability -> fails
    #    after {job_id}-markup.png copy; cleanup must remove it.
    _make_async_preset(client, monkeypatch, deepcopy(EDIT_MAPPING), default_model="gpt-image-2.6-flare")
    no_markup_resp = client.post(
        "/api/edits",
        data={"prompt": "no markup cap", "model": "gpt-image-2.6-flare"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "markup": ("markup.png", MARKUP_PNG, "image/png"),
        },
    )
    assert no_markup_resp.status_code == 422
    _assert_clean_disk_and_no_jobs(client)


# --------------------------------------------------------------------------- #
# E. paste_back outcomes (markup -> False, mask -> default, neither -> None)  #
# --------------------------------------------------------------------------- #


def test_edit_paste_back_outcomes_for_markup_mask_and_plain(client, monkeypatch):
    seen: list[dict] = []
    _install_generic_edit_stub(monkeypatch, seen)

    # 1. Markup forces payload.paste_back = False even when form sends paste_back=true,
    #    and job.paste_back is None (because mask_applied is False).
    markup_resp = client.post(
        "/api/edits",
        data={"prompt": "markup", "model": "gpt-image-2.6-flare", "paste_back": "true"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "markup": ("markup.png", MARKUP_PNG, "image/png"),
        },
    )
    assert markup_resp.status_code == 202
    markup_job = _wait_for_job(client, markup_resp.json()["job_id"])
    assert markup_job["markup_applied"] is True
    assert markup_job["mask_applied"] is False
    assert markup_job["paste_back"] is None
    assert seen[-1]["paste_back"] is False
    unit_req, _ = _persisted_unit_request(markup_job["job_id"])
    assert unit_req["paste_back"] is False

    # 2. Mask with omitted paste_back uses config.MASK_PASTE_BACK_DEFAULT (True then False).
    monkeypatch.setattr(config, "MASK_PASTE_BACK_DEFAULT", True)
    mask_default_true = client.post(
        "/api/edits",
        data={"prompt": "mask default true", "model": "gpt-image-2"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert mask_default_true.status_code == 202
    job_true = _wait_for_job(client, mask_default_true.json()["job_id"])
    assert job_true["mask_applied"] is True
    assert job_true["paste_back"] is True
    assert seen[-1]["paste_back"] is True
    assert _persisted_unit_request(job_true["job_id"])[0]["paste_back"] is True

    monkeypatch.setattr(config, "MASK_PASTE_BACK_DEFAULT", False)
    mask_default_false = client.post(
        "/api/edits",
        data={"prompt": "mask default false", "model": "gpt-image-2"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert mask_default_false.status_code == 202
    job_false = _wait_for_job(client, mask_default_false.json()["job_id"])
    assert job_false["mask_applied"] is True
    assert job_false["paste_back"] is False
    assert seen[-1]["paste_back"] is False
    assert _persisted_unit_request(job_false["job_id"])[0]["paste_back"] is False

    # 3. Neither mask nor markup -> job.paste_back is None (even if explicit paste_back=true).
    plain_omitted = client.post(
        "/api/edits",
        data={"prompt": "plain omitted", "model": "gpt-image-2"},
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert plain_omitted.status_code == 202
    job_plain_omitted = _wait_for_job(client, plain_omitted.json()["job_id"])
    assert job_plain_omitted["mask_applied"] is False
    assert job_plain_omitted["markup_applied"] is False
    assert job_plain_omitted["paste_back"] is None
    assert seen[-1]["paste_back"] is None
    assert _persisted_unit_request(job_plain_omitted["job_id"])[0]["paste_back"] is None

    plain_explicit = client.post(
        "/api/edits",
        data={"prompt": "plain explicit", "model": "gpt-image-2", "paste_back": "true"},
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert plain_explicit.status_code == 202
    job_plain_explicit = _wait_for_job(client, plain_explicit.json()["job_id"])
    assert job_plain_explicit["paste_back"] is None
    assert seen[-1]["paste_back"] is True
    assert _persisted_unit_request(job_plain_explicit["job_id"])[0]["paste_back"] is True


# --------------------------------------------------------------------------- #
# F. Persisted request_json parity matrix (for C1-S4 non-mutating copy)       #
# --------------------------------------------------------------------------- #


_DEFAULT_SNAPSHOT = {
    "api_url": "https://api.example.com",
    "provider_kind": "openai",
    "provider_config": None,
    "prompt_guard": False,
    "supports_mask": True,
}


def test_persisted_request_json_parity_matrix(client, monkeypatch):
    _install_generic_edit_stub(monkeypatch)
    monkeypatch.setattr(config, "MASK_PASTE_BACK_DEFAULT", True)

    # Case 1: Minimal /api/generate inherits preset default_model & default_response_format.
    resp1 = client.post("/api/generate", json={"prompt": "minimal"})
    assert resp1.status_code == 202
    job1 = _wait_for_job(client, resp1.json()["job_id"])
    expected1 = {
        "prompt": "minimal",
        "size": "auto",
        "model": "gpt-image-2",
        "n": 1,
        "quality": "auto",
        "output_format": "png",
        "complexity": None,
        "frame_count": None,
        "frame_duration_ms": None,
        "output_compression": None,
        "background": "auto",
        "response_format": "url",
        "api_path": None,
        "api_preset_id": None,
        "stream": False,
        "partial_images": 2,
        "safety_identifier": None,
        "_provider_snapshot": _DEFAULT_SNAPSHOT,
    }
    parsed1, raw1 = _persisted_unit_request(job1["job_id"])
    assert parsed1 == expected1
    assert raw1 == json.dumps(expected1, ensure_ascii=False, sort_keys=True)

    # Case 2: Preset default_model=2.5 + explicit response_format="b64_json" -> cleared to None.
    _save_active_preset(
        client,
        default_model="gpt-image-2.5-flare",
        default_response_format="url",
    )
    resp2 = client.post(
        "/api/generate",
        json={"prompt": "preset 25", "response_format": "b64_json", "output_format": "webp"},
    )
    assert resp2.status_code == 202
    job2 = _wait_for_job(client, resp2.json()["job_id"])
    expected2 = {
        **expected1,
        "prompt": "preset 25",
        "model": "gpt-image-2.5-flare",
        "output_format": "webp",
        "output_compression": 100,
        "response_format": None,
    }
    parsed2, raw2 = _persisted_unit_request(job2["job_id"])
    assert parsed2 == expected2
    assert raw2 == json.dumps(expected2, ensure_ascii=False, sort_keys=True)

    # Case 3: Explicit 2.6 GIF with n=2, complexity, safety_identifier trimming, gif defaults.
    resp3 = client.post(
        "/api/generate",
        json={
            "prompt": "gif 26",
            "model": "gpt-image-2.6-flare",
            "output_format": "gif",
            "complexity": "low",
            "n": 2,
            "safety_identifier": "  tenant-1  ",
        },
    )
    assert resp3.status_code == 202
    job3 = _wait_for_job(client, resp3.json()["job_id"])
    expected3 = {
        **expected1,
        "prompt": "gif 26",
        "model": "gpt-image-2.6-flare",
        "output_format": "gif",
        "complexity": "low",
        "frame_count": 9,
        "frame_duration_ms": 200,
        "response_format": None,
        "safety_identifier": "tenant-1",
    }
    parsed3, raw3 = _persisted_unit_request(job3["job_id"])
    assert parsed3 == expected3
    assert raw3 == json.dumps(expected3, ensure_ascii=False, sort_keys=True)

    # Case 4: Transparent + jpeg coerces to png; preset default_response_format="b64_json" preserved.
    _save_active_preset(client, default_model="gpt-image-2", default_response_format="b64_json")
    resp4 = client.post(
        "/api/generate",
        json={"prompt": "transparent coerce", "output_format": "jpeg", "background": "transparent"},
    )
    assert resp4.status_code == 202
    job4 = _wait_for_job(client, resp4.json()["job_id"])
    expected4 = {
        **expected1,
        "prompt": "transparent coerce",
        "output_format": "png",
        "background": "transparent",
        "response_format": "b64_json",
    }
    parsed4, raw4 = _persisted_unit_request(job4["job_id"])
    assert parsed4 == expected4
    assert raw4 == json.dumps(expected4, ensure_ascii=False, sort_keys=True)

    # Case 5: Pinned secondary preset on /api/generate.
    alt_id = _create_secondary_preset(
        client,
        "Alt 2.5",
        api_path="/v1/images/generations",
        default_model="gpt-image-2.5-sunburst",
        default_response_format="url",
    )
    resp5 = client.post("/api/generate", json={"prompt": "pinned", "api_preset_id": alt_id})
    assert resp5.status_code == 202
    job5 = _wait_for_job(client, resp5.json()["job_id"])
    expected5 = {
        **expected1,
        "prompt": "pinned",
        "model": "gpt-image-2.5-sunburst",
        "response_format": None,
        "api_preset_id": alt_id,
    }
    parsed5, raw5 = _persisted_unit_request(job5["job_id"])
    assert parsed5 == expected5
    assert raw5 == json.dumps(expected5, ensure_ascii=False, sort_keys=True)

    # Case 6: /api/edits with omitted model/response_format + mask -> inherits model,
    # keeps response_format=None (Form(None) puts response_format in model_fields_set),
    # and resolves paste_back=True.
    resp6 = client.post(
        "/api/edits",
        data={"prompt": "masked default"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert resp6.status_code == 202
    job6 = _wait_for_job(client, resp6.json()["job_id"])
    expected6 = {
        **expected1,
        "prompt": "masked default",
        "response_format": None,
        "paste_back": True,
    }
    parsed6, raw6 = _persisted_unit_request(job6["job_id"])
    assert parsed6 == expected6
    assert raw6 == json.dumps(expected6, ensure_ascii=False, sort_keys=True)

    # Case 6b: /api/edits with explicit response_format="url" on legacy model preserves it.
    resp6b = client.post(
        "/api/edits",
        data={"prompt": "edit explicit response_format", "response_format": "url"},
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert resp6b.status_code == 202
    job6b = _wait_for_job(client, resp6b.json()["job_id"])
    expected6b = {
        **expected1,
        "prompt": "edit explicit response_format",
        "response_format": "url",
        "paste_back": None,
    }
    parsed6b, raw6b = _persisted_unit_request(job6b["job_id"])
    assert parsed6b == expected6b
    assert raw6b == json.dumps(expected6b, ensure_ascii=False, sort_keys=True)

    # Case 7: /api/edits with 2.6 + markup + paste_back=true -> paste_back coerced to False.
    resp7 = client.post(
        "/api/edits",
        data={
            "prompt": "markup edit",
            "model": "gpt-image-2.6-sunburst",
            "complexity": "medium",
            "paste_back": "true",
        },
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "markup": ("markup.png", MARKUP_PNG, "image/png"),
        },
    )
    assert resp7.status_code == 202
    job7 = _wait_for_job(client, resp7.json()["job_id"])
    expected7 = {
        **expected1,
        "prompt": "markup edit",
        "model": "gpt-image-2.6-sunburst",
        "complexity": "medium",
        "response_format": None,
        "paste_back": False,
    }
    parsed7, raw7 = _persisted_unit_request(job7["job_id"])
    assert parsed7 == expected7
    assert raw7 == json.dumps(expected7, ensure_ascii=False, sort_keys=True)

    # Case 8: /api/edits with 2.6 GIF + no mask/markup.
    resp8 = client.post(
        "/api/edits",
        data={
            "prompt": "gif edit",
            "model": "gpt-image-2.6-flare",
            "output_format": "gif",
            "frame_count": "4",
            "frame_duration_ms": "100",
        },
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert resp8.status_code == 202
    job8 = _wait_for_job(client, resp8.json()["job_id"])
    expected8 = {
        **expected1,
        "prompt": "gif edit",
        "model": "gpt-image-2.6-flare",
        "output_format": "gif",
        "frame_count": 4,
        "frame_duration_ms": 100,
        "response_format": None,
        "paste_back": None,
    }
    parsed8, raw8 = _persisted_unit_request(job8["job_id"])
    assert parsed8 == expected8
    assert raw8 == json.dumps(expected8, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# G. Rule precedence when multiple rules fail simultaneously                  #
# --------------------------------------------------------------------------- #


def test_edit_rule_precedence_on_simultaneous_failures(client, monkeypatch):
    # 1. Form schema error (SVG is generation-only) runs before mask+markup check.
    r1 = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "svg"},
        files={
            "mask": ("mask.png", MASK_PNG, "image/png"),
            "markup": ("markup.png", MARKUP_PNG, "image/png"),
        },
    )
    assert r1.status_code == 422
    assert "SVG is generation-only" in r1.json()["detail"]

    # 2. Mask+markup combined runs before missing image and before non-2.6 markup check.
    r2 = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2"},
        files={
            "mask": ("mask.png", MASK_PNG, "image/png"),
            "markup": ("markup.png", MARKUP_PNG, "image/png"),
        },
    )
    assert r2.status_code == 422
    assert r2.json()["detail"] == "Mask and markup cannot be combined"

    # 3. Invalid mask MIME (JPEG) runs before missing-source check.
    r3 = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2"},
        files={"mask": ("mask.jpg", b"not-png", "image/jpeg")},
    )
    assert r3.status_code == 400
    assert r3.json()["detail"] == "Mask must be a PNG file."

    # 4. Non-2.6 model wins over wrong-sized / opaque markup in validate_uploaded_markup.
    r4 = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "markup": ("markup.png", WRONG_SIZE_PNG, "image/png"),
        },
    )
    assert r4.status_code == 422
    assert r4.json()["detail"] == "Markup requires GPT Image 2.6"

    # 5. Wrong-sized mask wins over GIF+mask (router validates mask before queue_edit_job).
    r5 = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif"},
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", WRONG_SIZE_PNG, "image/png"),
        },
    )
    assert r5.status_code == 422
    assert r5.json()["detail"] == "Mask dimensions must match the primary image: mask is 16x16, image is 8x8"

    # 6. GIF+mask wins over 2.6 GIF primary-input format check (queue_edit_job checks before queue_image_job).
    r6 = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif"},
        files={
            "image": ("primary.gif", _gif_bytes((8, 8)), "image/gif"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert r6.status_code == 422
    assert r6.json()["detail"] == "GIF output does not support edit masks"

    # 7. Missing api_preset_id wins over invalid model/quality options.
    r7 = client.post(
        "/api/generate",
        json={"prompt": "x", "api_preset_id": "nope", "model": "gpt-image-2", "quality": "max"},
    )
    assert r7.status_code == 422
    assert r7.json()["detail"] == "API preset not found"

    # 8. Async-provider GIF animation-template check wins over missing edit_submit check.
    no_edit = deepcopy(EDIT_MAPPING)
    no_edit.pop("edit_submit")
    _make_async_preset(client, monkeypatch, no_edit, default_model="gpt-image-2.6-flare")
    r8 = client.post(
        "/api/edits",
        data={"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif"},
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert r8.status_code == 422
    assert r8.json()["detail"] == "Provider mapping does not send animation controls for this operation"
    _assert_clean_disk_and_no_jobs(client)


def test_generate_model_option_rule_precedence(client):
    # api_path check precedes complexity check on 2.5.
    r1 = client.post(
        "/api/generate",
        json={
            "prompt": "x",
            "model": "gpt-image-2.5-flare",
            "api_path": "/v1/responses",
            "complexity": "low",
        },
    )
    assert r1.status_code == 422
    assert r1.json()["detail"] == "GPT Image 2.5/2.6 requires /v1/images/generations or /v1/images/edits"

    # quality check precedes complexity check on gpt-image-2.
    r2 = client.post(
        "/api/generate",
        json={"prompt": "x", "model": "gpt-image-2", "quality": "max", "complexity": "low"},
    )
    assert r2.status_code == 422
    assert r2.json()["detail"] == "quality 'max' is not supported for model 'gpt-image-2'"
    _assert_clean_disk_and_no_jobs(client)


# --------------------------------------------------------------------------- #
# H. Latent role="reference" restore-layer behavior (pins current 409)        #
# --------------------------------------------------------------------------- #


def test_reference_only_edit_pins_current_409_on_layer_restore(client, monkeypatch):
    _install_generic_edit_stub(monkeypatch)
    gallery_mutations.add_to_gallery_sync(
        image_id="ref-only-primary",
        prompt="seed",
        size="8x8",
        filename="ref-only-primary.png",
        metadata={"model": "gpt-image-2", "output_format": "png"},
        image_bytes=SOURCE_PNG,
    )

    # Mask edit with reference_image_id only (no uploaded image, no gallery_primary_id)
    masked = client.post(
        "/api/edits",
        data={"prompt": "masked ref only", "model": "gpt-image-2", "reference_image_id": "ref-only-primary"},
        files={"mask": ("mask.png", MASK_PNG, "image/png")},
    )
    assert masked.status_code == 202, masked.text
    masked_job = _wait_for_job(client, masked.json()["job_id"])
    assert masked_job["status"] == "success"
    assert masked_job["mask_applied"] is True
    assert client.get(f"/api/generate/{masked_job['job_id']}/mask").status_code == 200

    units = aggregate_image_job_units(masked_job["job_id"])["units"]
    assert units[0]["edit_sources"][0]["role"] == "reference"

    # Current behavior: _verify_restore_layer_source filters edit_sources by role == "image",
    # so restoring mask for a reference-only edit returns 409.
    restore_mask_by_id = client.post(
        f"/api/generate/{masked_job['job_id']}/mask/restore",
        data={"gallery_image_id": "ref-only-primary"},
    )
    assert restore_mask_by_id.status_code == 409
    assert restore_mask_by_id.json()["detail"] == "Original edit source identity is unavailable"

    # Markup edit with reference_image_id only pins the same 409 behavior.
    marked = client.post(
        "/api/edits",
        data={
            "prompt": "markup ref only",
            "model": "gpt-image-2.6-flare",
            "reference_image_id": "ref-only-primary",
        },
        files={"markup": ("markup.png", MARKUP_PNG, "image/png")},
    )
    assert marked.status_code == 202, marked.text
    marked_job = _wait_for_job(client, marked.json()["job_id"])
    assert marked_job["status"] == "success"
    assert marked_job["markup_applied"] is True
    assert client.get(f"/api/generate/{marked_job['job_id']}/markup").status_code == 200

    restore_markup_by_id = client.post(
        f"/api/generate/{marked_job['job_id']}/markup/restore",
        data={"gallery_image_id": "ref-only-primary"},
    )
    assert restore_markup_by_id.status_code == 409
    assert restore_markup_by_id.json()["detail"] == "Original edit source identity is unavailable"
