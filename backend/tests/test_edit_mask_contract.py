import io

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

    masked = job_queue.edit_source_from_payload({**payload, "role": "mask"})
    assert masked.role == "mask"


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

    # The mask is copied out of DATA_DIR into MASKS_DIR under the job id, so it
    # stays available after the temp edit sources are cleaned up.
    mask_path = _mask_path_for(job_id)
    assert mask_path.exists()
    assert mask_path.read_bytes() == MASK_PNG
    assert seen["mask_coverage"] == 1.0

    # The startup sweep keeps masks that still have a job row.
    startup_maintenance.cleanup_orphan_mask_files()
    assert mask_path.exists()

    served = client.get(f"/api/generate/{job_id}/mask")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == MASK_PNG


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
