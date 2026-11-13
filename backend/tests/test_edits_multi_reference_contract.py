import io
from PIL import Image
from backend.tests.support.contract import *  # noqa: F403


def _png_bytes(size: tuple[int, int] = (8, 8), color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mask_png_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gif_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    img = Image.new("RGB", size, (0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


def _svg_bytes() -> bytes:
    return b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="red"/></svg>'


def test_edit_combines_uploads_and_gallery_references_in_strict_order(client, monkeypatch):
    _fake_gallery_entry("ref-1", "ref 1", "8x8", "ref-1.png")
    _fake_gallery_entry("ref-2", "ref 2", "8x8", "ref-2.png")
    seen: dict[str, list[str]] = {}

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
    ):
        seen["filenames"] = [source.filename for source in image_sources]
        seen["roles"] = [source.role for source in image_sources]
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

    resp = client.post(
        "/api/edits",
        data={
            "prompt": "combine uploads and references",
            "model": "gpt-image-2",
            "reference_image_id": ["ref-1", "ref-2"],
        },
        files=[
            ("image[]", ("first.png", PNG_BYTES, "image/png")),
            ("image[]", ("second.png", PNG_BYTES, "image/png")),
        ],
    )
    assert resp.status_code == 202, resp.json()
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert seen["filenames"] == ["first.png", "second.png", "ref-1.png", "ref-2.png"]
    assert seen["roles"] == ["image", "image", "image", "image"]


def test_edit_from_gallery_combines_primary_uploads_and_references(client, monkeypatch):
    _fake_gallery_entry("primary-img", "primary", "8x8", "primary-img.png")
    _fake_gallery_entry("extra-ref", "extra ref", "8x8", "extra-ref.png")
    seen: dict[str, list[str]] = {}

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
    ):
        seen["filenames"] = [source.filename for source in image_sources]
        seen["roles"] = [source.role for source in image_sources]
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

    resp = client.post(
        "/api/edits/from-gallery/primary-img",
        data={
            "prompt": "gallery primary with upload and reference",
            "model": "gpt-image-2",
            "reference_image_id": "extra-ref",
        },
        files={"image": ("upload.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 202, resp.json()
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert seen["filenames"] == ["primary-img.png", "upload.png", "extra-ref.png"]
    assert seen["roles"] == ["image", "image", "image"]


def test_edit_supports_only_gallery_references_without_uploads(client, monkeypatch):
    _fake_gallery_entry("solo-1", "solo 1", "8x8", "solo-1.png")
    _fake_gallery_entry("solo-2", "solo 2", "8x8", "solo-2.png")
    seen: dict[str, list[str]] = {}

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
    ):
        seen["filenames"] = [source.filename for source in image_sources]
        seen["roles"] = [source.role for source in image_sources]
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

    resp = client.post(
        "/api/edits",
        data={
            "prompt": "only gallery references",
            "model": "gpt-image-2",
            "reference_image_id[]": ["solo-1", "solo-2"],
        },
    )
    assert resp.status_code == 202, resp.json()
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert seen["filenames"] == ["solo-1.png", "solo-2.png"]
    assert seen["roles"] == ["image", "image"]


def test_edit_pre_staging_count_validation(client):
    # Total > 16 rejects with 400
    resp = client.post(
        "/api/edits",
        data={
            "prompt": "over limit",
            "reference_image_id": [f"ref-{i}" for i in range(10)],
        },
        files=[("image[]", (f"upload-{i}.png", PNG_BYTES, "image/png")) for i in range(7)],
    )
    assert resp.status_code == 400
    assert "At most 16 edit source images are supported." in resp.json()["detail"]

    # from-gallery with 1 primary + 10 uploads + 6 references = 17 > 16
    resp_gallery = client.post(
        "/api/edits/from-gallery/some-id",
        data={
            "prompt": "over limit gallery",
            "reference_image_id": [f"ref-{i}" for i in range(6)],
        },
        files=[("image[]", (f"upload-{i}.png", PNG_BYTES, "image/png")) for i in range(10)],
    )
    assert resp_gallery.status_code == 400
    assert "At most 16 edit source images are supported." in resp_gallery.json()["detail"]


def test_edit_rejects_missing_gallery_reference(client):
    resp = client.post(
        "/api/edits",
        data={
            "prompt": "test missing",
            "model": "gpt-image-2",
            "reference_image_id": "non-existent-img-id",
        },
        files={"image": ("up.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 404
    assert "Gallery entry not found" in resp.json()["detail"]


def test_edit_rejects_svg_and_gif_as_references(client):
    # Seed SVG and GIF entries
    gallery_mutations.add_to_gallery_sync(
        image_id="svg-ref",
        prompt="svg icon",
        size="10x10",
        filename="svg-ref.svg",
        metadata={"model": "gpt-image-2"},
        image_bytes=_svg_bytes(),
    )
    gallery_mutations.add_to_gallery_sync(
        image_id="gif-ref",
        prompt="gif anim",
        size="8x8",
        filename="gif-ref.gif",
        metadata={"model": "gpt-image-2"},
        image_bytes=_gif_bytes(),
    )

    resp_svg = client.post(
        "/api/edits",
        data={
            "prompt": "using svg as ref",
            "model": "gpt-image-2",
            "reference_image_id": "svg-ref",
        },
        files={"image": ("up.png", PNG_BYTES, "image/png")},
    )
    assert resp_svg.status_code == 400
    assert "SVG and GIF images cannot be used as reference images" in resp_svg.json()["detail"]

    resp_gif = client.post(
        "/api/edits",
        data={
            "prompt": "using gif as ref",
            "model": "gpt-image-2",
            "reference_image_id": "gif-ref",
        },
        files={"image": ("up.png", PNG_BYTES, "image/png")},
    )
    assert resp_gif.status_code == 400
    assert "SVG and GIF images cannot be used as reference images" in resp_gif.json()["detail"]


def test_edit_mask_preserved_with_multiple_references(client, monkeypatch):
    # Primary image is 8x8; mask is 8x8; reference is 16x16.
    # Mask must validate against primary (8x8) and NOT fail against 16x16 reference.
    gallery_mutations.add_to_gallery_sync(
        image_id="primary-mask-img",
        prompt="primary",
        size="8x8",
        filename="primary-mask-img.png",
        metadata={"model": "gpt-image-2"},
        image_bytes=_png_bytes(size=(8, 8)),
    )
    # Seed 16x16 reference entry
    gallery_mutations.add_to_gallery_sync(
        image_id="large-ref",
        prompt="large reference",
        size="16x16",
        filename="large-ref.png",
        metadata={"model": "gpt-image-2"},
        image_bytes=_png_bytes(size=(16, 16)),
    )

    seen = {}

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
    ):
        seen["mask_present"] = mask_source is not None
        seen["image_sources_count"] = len(image_sources)
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

    resp = client.post(
        "/api/edits/from-gallery/primary-mask-img",
        data={
            "prompt": "masked edit with large reference",
            "model": "gpt-image-2",
            "reference_image_id": "large-ref",
        },
        files={"mask": ("mask.png", _mask_png_bytes((8, 8)), "image/png")},
    )
    assert resp.status_code == 202, resp.json()
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert seen["mask_present"] is True
    assert seen["image_sources_count"] == 2


def test_edit_early_validation_failure_cleans_up_staged_mask_and_markup(client):
    from pathlib import Path
    from backend.app.core import settings as app_config

    temp_dir = Path(app_config.DATA_DIR) / "edit-sources"
    before = set(temp_dir.glob("*")) if temp_dir.exists() else set()

    missing_sources_resp = client.post(
        "/api/edits",
        data={"prompt": "mask without sources", "model": "gpt-image-2"},
        files={"mask": ("mask.png", _mask_png_bytes((8, 8)), "image/png")},
    )
    assert missing_sources_resp.status_code == 422

    too_many_resp = client.post(
        "/api/edits",
        data={
            "prompt": "markup with too many refs",
            "model": "gpt-image-2.6-flare",
            "reference_image_id": [f"missing-ref-{idx}" for idx in range(17)],
        },
        files={"markup": ("markup.png", _mask_png_bytes((8, 8)), "image/png")},
    )
    assert too_many_resp.status_code == 400

    after = set(temp_dir.glob("*")) if temp_dir.exists() else set()
    assert after == before
