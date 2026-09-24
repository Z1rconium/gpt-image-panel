import io
import random

from PIL import Image as PILImage

from backend.tests.support.contract import *  # noqa: F403


def _png_bytes(
    size: tuple[int, int] = (8, 8),
    mode: str = "RGB",
    fill_alpha: int = 255,
) -> bytes:
    image = PILImage.new(mode, size, (255, 0, 0, 255) if mode == "RGBA" else (255, 0, 0))
    if mode == "RGBA":
        image.putalpha(fill_alpha)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


SOURCE_PNG = _png_bytes((8, 8), "RGB")
MASK_PNG = _png_bytes((8, 8), "RGBA", fill_alpha=0)
OPAQUE_MASK_PNG = _png_bytes((8, 8), "RGBA", fill_alpha=255)
RGB_MASK_PNG = _png_bytes((8, 8), "RGB")
WRONG_SIZE_MASK_PNG = _png_bytes((16, 16), "RGBA", fill_alpha=0)


def _seed_gallery_entry(image_id: str, image_bytes: bytes):
    gallery_mutations.add_to_gallery_sync(
        image_id=image_id,
        prompt="seed image",
        size="1024x1024",
        filename=f"{image_id}.png",
        metadata={
            "model": "gpt-image-2",
            "quality": "auto",
            "output_format": "png",
            "n": 1,
            "api_path": "/v1/images/generations",
            "api_preset_name": "Default",
        },
        image_bytes=image_bytes,
    )
    return gallery_queries.get_gallery_entry(image_id)


def _edit_data():
    return {
        "prompt": "masked edit prompt",
        "model": "gpt-image-2",
        "n": 1,
        "quality": "auto",
        "output_format": "png",
    }


async def _fake_entry(payload, api_preset_name):
    image_id = media.generate_image_id()
    filename = f"{image_id}.png"
    return await gallery_mutations.add_to_gallery_async(
        image_bytes=PNG_BYTES,
        image_id=image_id,
        prompt=payload.prompt,
        size=payload.size,
        filename=filename,
        metadata={"api_path": "/v1/images/edits", "api_preset_name": api_preset_name},
    )


def test_edit_accepts_valid_mask_and_marks_job(client, monkeypatch):
    seen: dict[str, object] = {}

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
        assert len(image_sources) == 1
        seen["image_role"] = image_sources[0].role
        assert mask_source is not None
        assert mask_source.role == "mask"
        assert mask_source.content_type == "image/png"
        seen["mask_path"] = mask_source.temp_path
        seen["image_path"] = image_sources[0].temp_path
        assert mask_source.temp_path.exists()
        assert mask_source.temp_path.read_bytes() == MASK_PNG
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    edit = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )

    assert edit.status_code == 202
    job_id = edit.json()["job_id"]
    assert client.get(f"/api/generate/{job_id}").json()["mask_applied"] is True
    job = _wait_for_job(client, job_id)
    assert job["status"] == "success"
    assert job["operation"] == "edit"
    assert job["mask_applied"] is True
    assert seen["image_role"] == "image"

    deadline = time.time() + 5
    while time.time() < deadline:
        if (
            not Path(seen["mask_path"]).exists()
            and not Path(seen["image_path"]).exists()
            and job_queue.get_pending_edit_source_bytes() == 0
        ):
            break
        time.sleep(0.05)
    assert not Path(seen["mask_path"]).exists()
    assert not Path(seen["image_path"]).exists()
    assert job_queue.get_pending_edit_source_bytes() == 0


def test_masked_edit_decodes_primary_and_mask_exactly_once(client, monkeypatch):
    """Admission used to decode the primary twice and the mask three times
    (see mask-performance-optimization-plan.md B1/B2); the primary's size is
    now cached on EditImageSource at admission and reused, and the mask is
    validated with a single Image.open + load."""
    opens: list[object] = []
    original_open = PILImage.open

    def counting_open(fp, *args, **kwargs):
        opens.append(fp)
        return original_open(fp, *args, **kwargs)

    monkeypatch.setattr(PILImage, "open", counting_open)

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
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert resp.status_code == 202
    assert len(opens) == 2


def _palette_trns_mask_png(size: tuple[int, int] = (8, 8)) -> bytes:
    image = PILImage.new("P", size, 0)
    image.putpalette([0, 0, 0] * 256)
    image.info["transparency"] = 0
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


PALETTE_TRNS_MASK_PNG = _palette_trns_mask_png()


def test_edit_accepts_palette_mask_with_trns_transparency(client, monkeypatch):
    seen: dict[str, object] = {}

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
        seen["mask_coverage"] = mask_coverage
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", PALETTE_TRNS_MASK_PNG, "image/png"),
        },
    )
    assert resp.status_code == 202
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert seen["mask_coverage"] == 1.0


def test_mask_optimized_png_is_a_smaller_grayscale_alpha_reencode(tmp_path):
    """S1: the RGB behind the alpha punch-out never reaches the upstream
    contract, so re-encoding a mask as flat-black `LA` for the MASKS_DIR retry
    copy should shrink it a lot whenever the uploaded RGB channel carries real
    entropy (e.g. an arbitrary user-uploaded PNG, not our own flat export)."""
    from backend.app.services import edit_masks

    size = (256, 256)
    rng = random.Random(20260921)
    image = PILImage.new("RGBA", size)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            alpha = 0 if (x // 16 + y // 16) % 2 == 0 else 255
            pixels[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256), alpha)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    noisy_mask_bytes = buffer.getvalue()

    mask_path = tmp_path / "mask.png"
    mask_path.write_bytes(noisy_mask_bytes)

    info = edit_masks.validate_edit_mask_file(
        mask_path, expected_width=size[0], expected_height=size[1]
    )
    assert info.transparent_ratio == pytest.approx(0.5, abs=0.01)
    assert info.optimized_png[25] == 4  # PNG IHDR color type: grayscale + alpha
    assert len(info.optimized_png) < len(noisy_mask_bytes) / 3

    with PILImage.open(io.BytesIO(info.optimized_png)) as optimized:
        assert optimized.mode == "LA"
        assert optimized.getchannel("A").tobytes() == image.getchannel("A").tobytes()


def test_edit_without_mask_reports_mask_not_applied(client, monkeypatch):
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
        assert mask_source is None
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    edit = client.post(
        "/api/edits",
        data=_edit_data(),
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )

    assert edit.status_code == 202
    job = _wait_for_job(client, edit.json()["job_id"])
    assert job["status"] == "success"
    assert job["mask_applied"] is False


def test_edit_from_gallery_validates_mask_against_gallery_image(client, monkeypatch):
    entry = _seed_gallery_entry("gallery-masked", SOURCE_PNG)
    assert entry is not None
    seen: dict[str, object] = {}

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
        assert mask_source is not None
        seen["mask"] = mask_source
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    ok = client.post(
        "/api/edits/from-gallery/gallery-masked",
        data=_edit_data(),
        files={
            "image": ("upload-16.png", WRONG_SIZE_MASK_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert ok.status_code == 202
    job = _wait_for_job(client, ok.json()["job_id"])
    assert job["status"] == "success"
    assert job["mask_applied"] is True

    # The same mask matches the uploaded 16x16 reference but not the 8x8
    # gallery primary, so the gallery route must reject it.
    mismatch = client.post(
        "/api/edits/from-gallery/gallery-masked",
        data=_edit_data(),
        files={
            "image": ("upload-16.png", WRONG_SIZE_MASK_PNG, "image/png"),
            "mask": ("mask.png", WRONG_SIZE_MASK_PNG, "image/png"),
        },
    )
    assert mismatch.status_code == 422
    assert "16x16" in mismatch.json()["detail"]
    assert "8x8" in mismatch.json()["detail"]


def _exif_orientedjpeg(size: tuple[int, int], orientation: int) -> bytes:
    """A JPEG whose EXIF Orientation says the picture is displayed rotated.

    Browsers honour the tag (`naturalWidth/Height` and `createImageBitmap`
    return the rotated size) while Pillow reports the stored one, which is the
    mismatch the orientation normalization closes.
    """
    image = PILImage.new("RGB", size, (255, 0, 0))
    exif = PILImage.Exif()
    exif[274] = orientation
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95, exif=exif)
    return buffer.getvalue()


EXIF6_JPEG = _exif_orientedjpeg((400, 300), 6)
EXIF6_ROTATED_MASK_PNG = _png_bytes((300, 400), "RGBA", fill_alpha=0)
PLAIN_JPEG = _exif_orientedjpeg((400, 300), 1)


def _capture_edit_sources(seen: dict[str, object]):
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
        primary = image_sources[0]
        seen["size"] = (primary.width, primary.height)
        seen["bytes"] = primary.temp_path.read_bytes()
        seen["byte_size"] = primary.byte_size
        return [await _fake_entry(payload, api_preset_name)]

    return fake_edit_api


def test_exif_oriented_primary_is_normalized_for_a_rotated_mask(client, monkeypatch):
    """E1: an EXIF-6 photo is written back upright, so what the browser showed,
    what the mask was drawn against and what upstream receives all agree."""
    seen: dict[str, object] = {}
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", _capture_edit_sources(seen))

    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("phone.jpg", EXIF6_JPEG, "image/jpeg"),
            "mask": ("mask.png", EXIF6_ROTATED_MASK_PNG, "image/png"),
        },
    )

    assert resp.status_code == 202
    assert _wait_for_job(client, resp.json()["job_id"])["status"] == "success"
    assert seen["size"] == (300, 400)
    # The reserved pending-upload bytes track the file that is actually sent.
    assert seen["byte_size"] == len(seen["bytes"])
    assert seen["bytes"] != EXIF6_JPEG
    with PILImage.open(io.BytesIO(seen["bytes"])) as sent:
        assert sent.size == (300, 400)
        # `exif_transpose` drops the tag, so a reader that also honours EXIF
        # cannot rotate the picture a second time.
        assert sent.getexif().get(274) in (None, 1)


def test_exif_oriented_primary_keeps_raw_orientation_for_a_raw_mask(client, monkeypatch):
    """The compatibility branch: a mask that only fits the stored orientation
    was drawn by a client that never saw the EXIF tag, so the bytes stay as they
    were and the mask keeps matching."""
    seen: dict[str, object] = {}
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", _capture_edit_sources(seen))

    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("phone.jpg", EXIF6_JPEG, "image/jpeg"),
            "mask": ("mask.png", _png_bytes((400, 300), "RGBA", fill_alpha=0), "image/png"),
        },
    )

    assert resp.status_code == 202
    assert _wait_for_job(client, resp.json()["job_id"])["status"] == "success"
    assert seen["size"] == (400, 300)
    assert seen["bytes"] == EXIF6_JPEG


def test_exif_free_primary_bytes_are_not_rewritten(client, monkeypatch):
    """A file without an orientation tag is forwarded exactly as uploaded."""
    seen: dict[str, object] = {}

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
        seen["bytes"] = image_sources[0].temp_path.read_bytes()
        seen["size"] = (image_sources[0].width, image_sources[0].height)
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("plain.jpg", PLAIN_JPEG, "image/jpeg"),
            "mask": ("mask.png", _png_bytes((400, 300), "RGBA", fill_alpha=0), "image/png"),
        },
    )

    assert resp.status_code == 202
    assert _wait_for_job(client, resp.json()["job_id"])["status"] == "success"
    assert seen["bytes"] == PLAIN_JPEG
    assert seen["size"] == (400, 300)


def test_gallery_source_is_normalized_for_edits(client, monkeypatch):
    """A gallery photo imported with EXIF orientation is normalized too."""
    gallery_mutations.add_to_gallery_sync(
        image_id="gallery-exif",
        prompt="seed oriented photo",
        size="1024x1024",
        filename="gallery-exif.jpg",
        metadata={
            "model": "gpt-image-2",
            "quality": "auto",
            "output_format": "jpeg",
            "n": 1,
            "api_path": "/v1/images/generations",
            "api_preset_name": "Default",
        },
        image_bytes=EXIF6_JPEG,
    )
    seen: dict[str, object] = {}

    async def capture_edit_api(
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
        seen["size"] = (image_sources[0].width, image_sources[0].height)
        seen["bytes"] = image_sources[0].temp_path.read_bytes()
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", capture_edit_api)

    resp = client.post(
        "/api/edits/from-gallery/gallery-exif",
        data=_edit_data(),
        files={"mask": ("mask.png", EXIF6_ROTATED_MASK_PNG, "image/png")},
    )

    assert resp.status_code == 202
    assert _wait_for_job(client, resp.json()["job_id"])["status"] == "success"
    assert seen["size"] == (300, 400)
    with PILImage.open(io.BytesIO(seen["bytes"])) as sent:
        assert sent.size == (300, 400)
        assert sent.getexif().get(274) in (None, 1)
    # The stored gallery file itself is untouched: only the edit-source copy is
    # re-encoded, so retries and thumbnails keep the original bytes.
    stored = Path(config.IMAGES_DIR) / "gallery-exif.jpg"
    assert stored.read_bytes() == EXIF6_JPEG


def test_edit_rejects_mask_with_wrong_dimensions(client):
    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", WRONG_SIZE_MASK_PNG, "image/png"),
        },
    )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "mask" in detail.lower()
    assert "16x16" in detail
    assert "8x8" in detail


def test_edit_rejects_mask_without_alpha_channel(client):
    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", RGB_MASK_PNG, "image/png"),
        },
    )

    assert resp.status_code == 422
    assert "alpha" in resp.json()["detail"]


def test_edit_rejects_fully_opaque_mask(client):
    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", OPAQUE_MASK_PNG, "image/png"),
        },
    )

    assert resp.status_code == 422
    assert "fully transparent" in resp.json()["detail"]


def test_edit_rejects_mask_that_is_jpeg(client):
    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", JPEG_BYTES, "image/png"),
        },
    )

    assert resp.status_code == 400


def test_edit_rejects_oversized_mask(client):
    oversized = MASK_PNG + (b"\0" * (4 * 1024 * 1024))
    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", oversized, "image/png"),
        },
    )

    assert resp.status_code == 400
    assert "4 MB" in resp.json()["detail"]


def test_edit_rejects_multiple_mask_fields(client):
    resp = client.post(
        "/api/edits",
        data=_edit_data(),
        files=[
            ("image", ("input.png", SOURCE_PNG, "image/png")),
            ("mask", ("mask-1.png", MASK_PNG, "image/png")),
            ("mask", ("mask-2.png", MASK_PNG, "image/png")),
        ],
    )

    assert resp.status_code == 400
    assert "Only one mask is supported" in resp.json()["detail"]


def test_mask_does_not_count_against_edit_source_limit(client, monkeypatch):
    seen: dict[str, object] = {}

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
        seen["count"] = len(image_sources)
        seen["mask"] = mask_source
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    edit = client.post(
        "/api/edits",
        data=_edit_data(),
        files=[
            *(
                ("image[]", (f"source-{index}.png", SOURCE_PNG, "image/png"))
                for index in range(16)
            ),
            ("mask", ("mask.png", MASK_PNG, "image/png")),
        ],
    )

    assert edit.status_code == 202
    job = _wait_for_job(client, edit.json()["job_id"])
    assert job["status"] == "success"
    assert seen["count"] == 16
    assert seen["mask"] is not None


def test_upstream_edit_form_includes_mask_field(tmp_path, monkeypatch):
    from backend.app.integrations.upstream import generation as upstream_client

    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    mask_path = tmp_path / "mask.png"
    first_path.write_bytes(PNG_BYTES)
    second_path.write_bytes(PNG_BYTES)
    mask_path.write_bytes(MASK_PNG)
    sources = [
        EditImageSource(first_path, len(PNG_BYTES), "first.png", "image/png"),
        EditImageSource(second_path, len(PNG_BYTES), "second.png", "image/png"),
    ]
    mask = EditImageSource(mask_path, len(MASK_PNG), "mask.png", "image/png", "mask")
    response_body = json.dumps(
        {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}
    ).encode("utf-8")
    session = _FakePostSession(
        _FakeResponse(
            200,
            headers={"Content-Type": "application/json"},
            chunks=[response_body],
            peer_ip="93.184.216.34",
        )
    )

    monkeypatch.setattr(upstream_client, "get_pool", lambda: _FakePool(session))
    monkeypatch.setattr(upstream_client.ssrf, "validate_upstream_url", lambda *args, **kwargs: None)
    monkeypatch.setattr(upstream_client.ssrf, "validate_response_peer_ip", lambda *args, **kwargs: None)

    entries = asyncio.run(
        ORIGINAL_CALL_IMAGE_EDIT_API(
            "https://api.example.com",
            "test-key",
            EditRequest(prompt="mask field test", model="gpt-image-2"),
            sources,
            persist_gallery_entry=gallery_mutations.add_to_gallery_async,
            mask_source=mask,
        )
    )

    assert len(entries) == 1
    assert session.requested_url == "https://api.example.com/v1/images/edits"
    fields = [(options["name"], options.get("filename")) for options, _headers, _value in session.data._fields]
    assert fields.count(("image[]", "first.png")) == 1
    assert fields.count(("image[]", "second.png")) == 1
    assert ("mask", "mask.png") in fields
    assert ("image", "first.png") not in fields


def test_upstream_masked_edit_paste_back_applies_or_preserves_raw(tmp_path, monkeypatch):
    from backend.app.integrations.upstream import generation as upstream_client

    _configure_runtime(tmp_path)
    primary_path = tmp_path / "primary.png"
    mask_path = tmp_path / "mask.png"
    primary_path.write_bytes(_png_bytes((64, 64)))
    mask_image = PILImage.new("RGBA", (64, 64), (0, 0, 0, 255))
    for y in range(16, 48):
        for x in range(16, 48):
            mask_image.putpixel((x, y), (0, 0, 0, 0))
    mask_buffer = io.BytesIO()
    mask_image.save(mask_buffer, format="PNG")
    mask_path.write_bytes(mask_buffer.getvalue())
    result_image = PILImage.new("RGB", (64, 64), (255, 0, 0))
    for y in range(16, 48):
        for x in range(10, 48):
            result_image.putpixel((x, y), (0, 200, 0))
    result_buffer = io.BytesIO()
    result_image.save(result_buffer, format="PNG")
    result_bytes = result_buffer.getvalue()
    source = EditImageSource(primary_path, primary_path.stat().st_size, "primary.png", "image/png", width=64, height=64)
    mask = EditImageSource(mask_path, mask_path.stat().st_size, "mask.png", "image/png", "mask")
    response_body = json.dumps(
        {"data": [{"b64_json": base64.b64encode(result_bytes).decode("ascii")}]}
    ).encode("utf-8")

    async def run(paste_back):
        session = _FakePostSession(
            _FakeResponse(200, headers={"Content-Type": "application/json"}, chunks=[response_body], peer_ip="93.184.216.34")
        )
        monkeypatch.setattr(upstream_client, "get_pool", lambda: _FakePool(session))
        entries = await ORIGINAL_CALL_IMAGE_EDIT_API(
            "https://api.example.com",
            "test-key",
            EditRequest(prompt="paste back test", model="gpt-image-2", paste_back=paste_back),
            [source],
            persist_gallery_entry=gallery_mutations.add_to_gallery_async,
            mask_source=mask,
        )
        return entries[0]

    applied = asyncio.run(run(True))
    assert applied.paste_back == "applied"
    assert applied.paste_back_scale == 1
    with PILImage.open(Path(config.IMAGES_DIR) / applied.filename) as image:
        assert image.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
        assert image.convert("RGB").getpixel((32, 32)) == (0, 200, 0)
        assert image.convert("RGB").getpixel((10, 32))[0] > 0

    disabled = asyncio.run(run(False))
    assert disabled.paste_back == "skipped:disabled"
    with PILImage.open(Path(config.IMAGES_DIR) / disabled.filename) as image:
        assert image.convert("RGB").getpixel((10, 32)) == (0, 200, 0)


def test_edit_paste_back_request_default_override_and_no_mask(client, monkeypatch):
    seen: list[tuple[bool | None, bool]] = []

    async def fake_edit_api(
        api_url, api_key, payload, image_sources, api_preset_name=None,
        progress=None, socks5_proxy=None, persist_gallery_entry=None,
        mask_source=None, mask_coverage=None,
    ):
        seen.append((payload.paste_back, mask_source is not None))
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)
    files = {
        "image": ("input.png", SOURCE_PNG, "image/png"),
        "mask": ("mask.png", MASK_PNG, "image/png"),
    }

    default = client.post("/api/edits", data=_edit_data(), files=files)
    assert default.status_code == 202
    assert _wait_for_job(client, default.json()["job_id"])["paste_back"] is True

    disabled = client.post("/api/edits", data={**_edit_data(), "paste_back": "false"}, files=files)
    assert disabled.status_code == 202
    assert _wait_for_job(client, disabled.json()["job_id"])["paste_back"] is False

    monkeypatch.setattr(config, "MASK_PASTE_BACK_DEFAULT", False)
    server_disabled = client.post("/api/edits", data=_edit_data(), files=files)
    assert server_disabled.status_code == 202
    assert _wait_for_job(client, server_disabled.json()["job_id"])["paste_back"] is False

    unmasked = client.post(
        "/api/edits",
        data={**_edit_data(), "paste_back": "true"},
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert unmasked.status_code == 202
    assert _wait_for_job(client, unmasked.json()["job_id"])["paste_back"] is None
    assert seen == [(True, True), (False, True), (False, True), (True, False)]


def test_gallery_exif_masked_edit_pastes_result_at_upright_size(client, monkeypatch):
    from backend.app.integrations.upstream import generation as upstream_client

    gallery_mutations.add_to_gallery_sync(
        image_id="paste-exif-source",
        prompt="seed oriented photo",
        size="auto",
        filename="paste-exif-source.jpg",
        metadata={"output_format": "jpeg", "api_path": "/v1/images/generations"},
        image_bytes=EXIF6_JPEG,
    )
    result = _png_bytes((300, 400))
    response_body = json.dumps(
        {"data": [{"b64_json": base64.b64encode(result).decode("ascii")}]}
    ).encode("utf-8")
    session = _FakePostSession(
        _FakeResponse(200, headers={"Content-Type": "application/json"}, chunks=[response_body], peer_ip="93.184.216.34")
    )
    monkeypatch.setattr(upstream_client, "get_pool", lambda: _FakePool(session))
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", ORIGINAL_CALL_IMAGE_EDIT_API)

    response = client.post(
        "/api/edits/from-gallery/paste-exif-source",
        data=_edit_data(),
        files={"mask": ("mask.png", EXIF6_ROTATED_MASK_PNG, "image/png")},
    )
    assert response.status_code == 202
    saved_job = _wait_for_job(client, response.json()["job_id"])
    assert saved_job["status"] == "success"
    assert saved_job["paste_back"] is True
    assert saved_job["images"][0]["paste_back"] == "applied"
    assert (saved_job["images"][0]["image_width"], saved_job["images"][0]["image_height"]) == (300, 400)
    with PILImage.open(Path(config.IMAGES_DIR) / saved_job["images"][0]["filename"]) as output:
        assert output.size == (300, 400)
        assert output.convert("RGB").getpixel((150, 200)) == (255, 0, 0)


def test_edit_mask_bytes_count_in_pending_reservation_and_cleanup(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 20
    seen: dict[str, Path] = {}
    started = threading.Event()
    release_event = threading.Event()

    async def blocking_edit_api(
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
        assert len(image_sources) == 1
        assert mask_source is not None
        seen["mask"] = mask_source.temp_path
        seen["image"] = image_sources[0].temp_path
        started.set()
        await asyncio.to_thread(release_event.wait)
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", blocking_edit_api)

    with _test_client() as test_client:
        edit = test_client.post(
            "/api/edits",
            data=_edit_data(),
            files={
                "image": ("input.png", SOURCE_PNG, "image/png"),
                "mask": ("mask.png", MASK_PNG, "image/png"),
            },
        )

        assert edit.status_code == 202
        assert started.wait(timeout=5)
        assert job_queue.get_pending_edit_source_bytes() == len(SOURCE_PNG) + len(MASK_PNG)
        release_event.set()

        job = _wait_for_job(test_client, edit.json()["job_id"])
        assert job["status"] == "success"
        assert job["mask_applied"] is True

        deadline = time.time() + 5
        while time.time() < deadline:
            if (
                not seen["mask"].exists()
                and not seen["image"].exists()
                and job_queue.get_pending_edit_source_bytes() == 0
            ):
                break
            time.sleep(0.05)

        assert not seen["mask"].exists()
        assert not seen["image"].exists()
        assert job_queue.get_pending_edit_source_bytes() == 0


def test_cancelled_masked_edit_cleans_mask_temp_file(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 20
    seen: dict[str, Path] = {}
    started = threading.Event()
    release_event = threading.Event()

    async def blocking_edit_api(
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
        assert mask_source is not None
        seen["mask"] = mask_source.temp_path
        seen["image"] = image_sources[0].temp_path
        assert seen["mask"].exists()
        started.set()
        await asyncio.to_thread(release_event.wait)
        raise AssertionError("cancelled masked edit should not finish upstream call")

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", blocking_edit_api)

    with _test_client() as test_client:
        edit = test_client.post(
            "/api/edits",
            data=_edit_data(),
            files={
                "image": ("input.png", SOURCE_PNG, "image/png"),
                "mask": ("mask.png", MASK_PNG, "image/png"),
            },
        )

        assert edit.status_code == 202
        assert started.wait(timeout=5)
        assert job_queue.get_pending_edit_source_bytes() == len(SOURCE_PNG) + len(MASK_PNG)

        cancelled = test_client.delete(f"/api/generate/{edit.json()['job_id']}")
        assert cancelled.status_code == 200
        release_event.set()

        deadline = time.time() + 5
        while time.time() < deadline:
            if (
                not seen["mask"].exists()
                and not seen["image"].exists()
                and job_queue.get_pending_edit_source_bytes() == 0
            ):
                break
            time.sleep(0.05)

        job = test_client.get(f"/api/generate/{edit.json()['job_id']}").json()
        assert job["status"] == "cancelled"
        assert job["mask_applied"] is True
        assert not seen["mask"].exists()
        assert not seen["image"].exists()
        assert job_queue.get_pending_edit_source_bytes() == 0


def test_legacy_edit_source_payload_without_role_rebuilds_as_image():
    payload = {
        "temp_path": "/tmp/edit-source-legacy.png",
        "byte_size": 12,
        "filename": "legacy.png",
        "content_type": "image/png",
    }
    source = job_queue.edit_source_from_payload(payload)
    assert source.role == "image"
    # Rows persisted before width/height existed default to 0 ("unknown"),
    # never a guessed size that could silently defeat the primary-size reuse
    # in validate_edit_mask().
    assert source.width == 0
    assert source.height == 0

    masked = job_queue.edit_source_from_payload({**payload, "role": "mask"})
    assert masked.role == "mask"

    sized = job_queue.edit_source_from_payload({**payload, "width": 64, "height": 32})
    assert (sized.width, sized.height) == (64, 32)
    assert job_queue.edit_source_to_payload(sized)["width"] == 64
    assert job_queue.edit_source_to_payload(sized)["height"] == 32


def _mask_path_for(job_id: str) -> Path:
    return Path(config.MASKS_DIR) / f"{job_id}.png"


def _submit_masked_edit(client, monkeypatch, seen: dict | None = None) -> str:
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
        if seen is not None:
            seen["mask_source"] = mask_source
            seen["mask_coverage"] = mask_coverage
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)
    edit = client.post(
        "/api/edits",
        data=_edit_data(),
        files={
            "image": ("input.png", SOURCE_PNG, "image/png"),
            "mask": ("mask.png", MASK_PNG, "image/png"),
        },
    )
    assert edit.status_code == 202
    return edit.json()["job_id"]


def test_edit_persists_mask_file_and_serves_it(client, monkeypatch):
    seen: dict[str, object] = {}
    job_id = _submit_masked_edit(client, monkeypatch, seen)
    assert _wait_for_job(client, job_id)["status"] == "success"

    # The mask is re-encoded as LA (see EditMaskInfo.optimized_png / S1 in
    # mask-performance-optimization-plan.md) and copied out of DATA_DIR into
    # MASKS_DIR under the job id, so it stays available after the temp edit
    # sources are cleaned up.
    mask_path = _mask_path_for(job_id)
    assert mask_path.exists()
    persisted_bytes = mask_path.read_bytes()
    assert persisted_bytes != MASK_PNG
    # PNG color type byte (IHDR, offset 25); 4 = grayscale + alpha, which is
    # what maskDocument.ts's pngHasAlphaChannel() and Pillow both accept.
    assert persisted_bytes[25] == 4
    with PILImage.open(io.BytesIO(persisted_bytes)) as persisted:
        assert persisted.mode == "LA"
        assert persisted.size == (8, 8)
        # Round-trips the same editable region as the uploaded mask: alpha ==
        # 0 marks "edit this pixel" and nothing else about the re-encode
        # changes which pixels those are.
        with PILImage.open(io.BytesIO(MASK_PNG)) as original:
            assert persisted.getchannel("A").tobytes() == original.getchannel("A").tobytes()
    assert seen["mask_coverage"] == 1.0

    # The startup sweep keeps masks that still have a job row.
    startup_maintenance.cleanup_orphan_mask_files()
    assert mask_path.exists()

    served = client.get(f"/api/generate/{job_id}/mask")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == persisted_bytes
    # job_id is unique and the promoted file is never rewritten, so a retry
    # re-fetching the same job's mask can cache it indefinitely.
    assert served.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert served.headers["x-mask-coverage"] == "1.0"


def test_mask_endpoint_is_missing_for_unmasked_and_unknown_jobs(client, monkeypatch):
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
        assert mask_source is None
        return [await _fake_entry(payload, api_preset_name)]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)
    edit = client.post(
        "/api/edits",
        data=_edit_data(),
        files={"image": ("input.png", SOURCE_PNG, "image/png")},
    )
    assert edit.status_code == 202
    job_id = edit.json()["job_id"]
    _wait_for_job(client, job_id)

    assert client.get(f"/api/generate/{job_id}/mask").status_code == 404
    assert client.get("/api/generate/unknown-job/mask").status_code == 404


def test_clearing_job_history_removes_persisted_mask(client, monkeypatch):
    job_id = _submit_masked_edit(client, monkeypatch)
    _wait_for_job(client, job_id)
    mask_path = _mask_path_for(job_id)
    assert mask_path.exists()

    assert client.delete("/api/generate/jobs/history").status_code == 200
    assert not mask_path.exists()


def test_orphan_mask_files_are_removed_on_startup(client):
    masks_dir = Path(config.MASKS_DIR)
    masks_dir.mkdir(parents=True, exist_ok=True)
    orphan = masks_dir / "00000000-0000-0000-0000-000000000000.png"
    orphan.write_bytes(MASK_PNG)

    startup_maintenance.cleanup_orphan_mask_files()
    assert not orphan.exists()
