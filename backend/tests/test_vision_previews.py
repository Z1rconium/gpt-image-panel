"""Resource and cache regressions using real bounded worker threads."""

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from backend.app.core import settings as config
from backend.app.core.observability import metrics
from backend.app.services import agent_context, vision_previews as previews


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    previews.clear_preview_cache()
    monkeypatch.setattr(config, "AI_ASSISTANT_IMAGE_MAX_SIDE", 1024)
    monkeypatch.setattr(config, "AI_ASSISTANT_IMAGE_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 100000000)
    monkeypatch.setattr(config, "VISION_PREVIEW_MEMORY_BUDGET_MB", 32)
    yield
    previews.clear_preview_cache()


def image_file(tmp_path, name="image.png", size=(1024, 1024)):
    path = tmp_path / name
    Image.new("RGB", size, "navy").save(path)
    return path


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(poll(), 5)


def decoder(monkeypatch, *, started=None, release=None, fail_first=False, length=64):
    counts = {"calls": 0, "active": 0, "peak": 0, "limits": []}
    lock = threading.Lock()

    def prepare(path, *, limits=None):
        with lock:
            counts["calls"] += 1
            call = counts["calls"]
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
            counts["limits"].append(limits)
        try:
            if started:
                started.set()
            if release:
                assert release.wait(5), "decoder release timed out"
            else:
                time.sleep(0.03)
            if fail_first and call == 1:
                raise ValueError("unreadable image")
            return {"mime_type": "image/png", "b64": "a" * length, "bytes": 48}
        finally:
            with lock:
                counts["active"] -= 1

    monkeypatch.setattr(previews.assistant_client, "prepare_vision_preview", prepare)
    return counts


@pytest.mark.anyio
async def test_concurrent_misses_share_decode_and_survive_caller_cancel(tmp_path, monkeypatch):
    path = image_file(tmp_path)
    started, release = threading.Event(), threading.Event()
    counts = decoder(monkeypatch, started=started, release=release)
    first = asyncio.create_task(previews.load_preview_data_url(path))
    await until(started.is_set)
    shared_before = metrics.snapshot()["counters"].get("vision_preview.shared_decodes", 0)
    second = asyncio.create_task(previews.load_preview_data_url(path))
    await until(lambda: metrics.snapshot()["counters"].get("vision_preview.shared_decodes", 0) > shared_before)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert previews.preview_gauges()["vision_preview.decode_memory_bytes"] > 0
    release.set()
    result = await second
    assert counts["calls"] == 1
    assert result == await previews.load_preview_data_url(path)
    assert previews.preview_gauges()["vision_preview.decode_memory_bytes"] == 0
    assert previews.preview_gauges()["vision_preview.cache_bytes"] == sys.getsizeof(result[0])


@pytest.mark.anyio
async def test_strict_cumulative_budget_and_cpu_pool(tmp_path, monkeypatch):
    paths = [image_file(tmp_path, f"{i}.png") for i in range(6)]
    counts = decoder(monkeypatch)
    await asyncio.gather(*(previews.load_preview_data_url(path) for path in paths))
    assert counts["calls"] == 6
    assert counts["peak"] == 1  # Two 19 MiB decodes cannot fit in 32 MiB.
    previews.clear_preview_cache()
    monkeypatch.setattr(config, "VISION_PREVIEW_MEMORY_BUDGET_MB", 256)
    counts = decoder(monkeypatch)
    await asyncio.gather(*(previews.load_preview_data_url(path) for path in paths))
    assert 1 < counts["peak"] <= config.IMAGE_CPU_CONCURRENCY
    assert previews.preview_gauges()["vision_preview.decode_memory_bytes"] == 0


@pytest.mark.anyio
async def test_oversized_png_skipped_but_jpeg_draft_fits(tmp_path, monkeypatch):
    counts = decoder(monkeypatch)
    png = image_file(tmp_path, size=(2048, 2048))
    assert await previews.load_preview_data_url(png) is None
    assert counts["calls"] == 0
    jpeg = image_file(tmp_path, "large.jpg", size=(4096, 4096))
    assert await previews.load_preview_data_url(jpeg) is not None
    assert counts["calls"] == 1


@pytest.mark.anyio
async def test_failed_decode_can_retry(tmp_path, monkeypatch):
    path = image_file(tmp_path)
    counts = decoder(monkeypatch, fail_first=True)
    results = await asyncio.gather(*(previews.load_preview_data_url(path) for _ in range(3)), return_exceptions=True)
    assert all(isinstance(result, ValueError) for result in results)
    assert counts["calls"] == 1
    assert await previews.load_preview_data_url(path)
    assert counts["calls"] == 2


@pytest.mark.anyio
async def test_config_snapshot_and_cache_invalidation(tmp_path, monkeypatch):
    path = image_file(tmp_path)
    counts = decoder(monkeypatch)
    await previews.load_preview_data_url(path)
    for name, value in [("AI_ASSISTANT_IMAGE_MAX_SIDE", 512), ("AI_ASSISTANT_IMAGE_MAX_BYTES", 65536), ("MAX_IMAGE_PIXELS", 2000000)]:
        monkeypatch.setattr(config, name, value)
        await previews.load_preview_data_url(path)
    assert counts["calls"] == 4
    assert counts["limits"][-1] == (512, 65536, 2000000)
    path.touch()
    await previews.load_preview_data_url(path)
    assert counts["calls"] == 5


@pytest.mark.anyio
async def test_clear_during_decode_prevents_old_cache_fill(tmp_path, monkeypatch):
    path = image_file(tmp_path)
    started, release = threading.Event(), threading.Event()
    counts = decoder(monkeypatch, started=started, release=release)
    old = asyncio.create_task(previews.load_preview_data_url(path))
    await until(started.is_set)
    previews.clear_preview_cache()
    release.set()
    assert await old
    assert previews.preview_gauges()["vision_preview.cache_entries"] == 0
    await previews.load_preview_data_url(path)
    assert counts["calls"] == 2


@pytest.mark.anyio
async def test_ttl_lru_and_oversized_entry_accounting(tmp_path, monkeypatch):
    paths = [image_file(tmp_path, f"{i}.png", size=(32, 32)) for i in range(3)]
    counts = decoder(monkeypatch)
    monkeypatch.setattr(previews, "PREVIEW_CACHE_MAX_ENTRIES", 2)
    await previews.load_preview_data_url(paths[0])
    await previews.load_preview_data_url(paths[1])
    await previews.load_preview_data_url(paths[0])  # Touch LRU.
    await previews.load_preview_data_url(paths[2])
    await previews.load_preview_data_url(paths[0])
    assert counts["calls"] == 3
    await previews.load_preview_data_url(paths[1])
    assert counts["calls"] == 4
    assert previews.preview_gauges()["vision_preview.cache_entries"] == 2
    monkeypatch.setattr(previews, "PREVIEW_CACHE_TTL_SECONDS", -1)
    assert previews.preview_gauges()["vision_preview.cache_bytes"] == 0
    await previews.load_preview_data_url(paths[1])
    assert counts["calls"] == 5
    monkeypatch.setattr(previews, "PREVIEW_CACHE_TTL_SECONDS", 900)
    previews.clear_preview_cache()
    monkeypatch.setattr(previews, "PREVIEW_CACHE_MAX_BYTES", 100)
    result = await previews.load_preview_data_url(paths[0])
    assert sys.getsizeof(result[0]) > 100
    assert previews.preview_gauges()["vision_preview.cache_bytes"] == 0
    await previews.load_preview_data_url(paths[0])
    assert counts["calls"] == 7


@pytest.mark.anyio
@pytest.mark.parametrize("max_bytes,max_images,expected", [(48, None, 1), (1000, 4, 4), (0, None, 0)])
async def test_ordered_loading_stops_scheduling_at_limits(monkeypatch, max_bytes, max_images, expected):
    loaded_ids = []

    async def get_entry(callback, image_id, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(filename=image_id)

    async def get_path(callback, name):
        return name

    async def preview(path):
        loaded_ids.append(str(path))
        await asyncio.sleep(0)
        return "data:image/png;base64,a", 48

    monkeypatch.setattr(agent_context, "run_db_operation", get_entry)
    monkeypatch.setattr(agent_context, "run_file_operation", get_path)
    monkeypatch.setattr(previews, "load_preview_data_url", preview)
    images = [{"image_id": str(i)} for i in range(20)]
    loaded = await agent_context.load_preview_data_urls(images, max_total_bytes=max_bytes, max_images=max_images)
    assert [row["image_id"] for row, _url in loaded] == [str(i) for i in range(expected)]
    assert len(loaded_ids) <= expected + 2 if expected else not loaded_ids


@pytest.mark.anyio
async def test_decode_uses_captured_settings_when_live_config_changes(tmp_path, monkeypatch):
    path = image_file(tmp_path)
    started, release = threading.Event(), threading.Event()
    real = previews.assistant_client.prepare_vision_preview
    seen = []

    def prepare(path, *, limits):
        seen.append(limits)
        started.set()
        assert release.wait(5)
        return real(path, limits=limits)

    monkeypatch.setattr(previews.assistant_client, "prepare_vision_preview", prepare)
    pending = asyncio.create_task(previews.load_preview_data_url(path))
    await until(started.is_set)
    monkeypatch.setattr(config, "AI_ASSISTANT_IMAGE_MAX_SIDE", 256)
    monkeypatch.setattr(config, "AI_ASSISTANT_IMAGE_MAX_BYTES", 65536)
    release.set()
    assert await pending
    assert seen == [(1024, 1024 * 1024, 100000000)]
    await previews.load_preview_data_url(path)
    assert seen[-1] == (256, 65536, 100000000)


@pytest.mark.anyio
async def test_byte_limit_evicts_by_retained_string_size(tmp_path, monkeypatch):
    counts = decoder(monkeypatch)
    paths = [image_file(tmp_path, f"{i}.png", size=(32, 32)) for i in range(3)]
    result = await previews.load_preview_data_url(paths[0])
    per_string = sys.getsizeof(result[0])
    monkeypatch.setattr(previews, "PREVIEW_CACHE_MAX_BYTES", per_string * 2)
    await previews.load_preview_data_url(paths[1])
    await previews.load_preview_data_url(paths[2])
    assert previews.preview_gauges()["vision_preview.cache_bytes"] == per_string * 2
    assert previews.preview_gauges()["vision_preview.cache_entries"] == 2
    await previews.load_preview_data_url(paths[0])
    assert counts["calls"] == 4


@pytest.mark.anyio
async def test_loader_skips_unreadable_images_until_four_successes(monkeypatch):
    from types import SimpleNamespace

    async def db(callback, image_id, **kwargs):
        return SimpleNamespace(filename=image_id)

    async def path(callback, name):
        return name

    async def preview(path):
        if str(path) in {"0", "2"}:
            raise ValueError("unreadable")
        return "data:image/png;base64,a", 48

    monkeypatch.setattr(agent_context, "run_db_operation", db)
    monkeypatch.setattr(agent_context, "run_file_operation", path)
    monkeypatch.setattr(previews, "load_preview_data_url", preview)
    loaded = await agent_context.load_preview_data_urls([{"image_id": str(i)} for i in range(10)], max_images=4)
    assert [row["image_id"] for row, _url in loaded] == ["1", "3", "4", "5"]
