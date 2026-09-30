"""Shared, budgeted gallery previews with a bounded process-local LRU cache."""

import asyncio
import io
import sys
import threading
import time
import warnings
from collections import OrderedDict
from pathlib import Path

from ..core import settings as config
from ..core.observability import metrics
from ..core.media import configure_pillow_image_limits
from ..integrations import assistant_client
from ..runtime.blocking import run_file_operation, run_image_operation

PREVIEW_CACHE_MAX_ENTRIES = 32
PREVIEW_CACHE_MAX_BYTES = 24 * 1024 * 1024
PREVIEW_CACHE_TTL_SECONDS = 15 * 60.0

_CacheKey = tuple[str, int, int, int, int, int]
# key -> (stored_at, data_url, compressed_bytes, retained_string_bytes)
_cache: OrderedDict[_CacheKey, tuple[float, str, int, int]] = OrderedDict()
_cached_bytes = 0
_generation = 0
_lock = threading.Lock()


class _PreviewBudget:
    def __init__(self):
        self.used = 0
        self.condition = asyncio.Condition()

    async def acquire(self, weight: int) -> bool:
        started = time.monotonic()
        waited = False
        async with self.condition:
            while True:
                capacity = config.VISION_PREVIEW_MEMORY_BUDGET_MB * 1024 * 1024
                if weight > capacity:
                    metrics.increment("vision_preview.budget_skips")
                    return False
                if self.used + weight <= capacity:
                    self.used += weight
                    break
                if not waited:
                    metrics.increment("vision_preview.budget_waits")
                    waited = True
                # Also re-evaluate a live config change when no work completes.
                try:
                    await asyncio.wait_for(self.condition.wait(), 0.25)
                except asyncio.TimeoutError:
                    pass
        metrics.observe_ms("vision_preview.budget_wait", (time.monotonic() - started) * 1000)
        return True

    async def release(self, weight: int) -> None:
        async with self.condition:
            self.used -= weight
            self.condition.notify_all()


def _runtime() -> tuple[_PreviewBudget, dict]:
    loop = asyncio.get_running_loop()
    runtime = getattr(loop, "_gpt_vision_previews", None)
    if runtime is None:
        runtime = (_PreviewBudget(), {})
        setattr(loop, "_gpt_vision_previews", runtime)
    return runtime


def clear_preview_cache() -> None:
    global _cached_bytes, _generation
    with _lock:
        _generation += 1
        _cache.clear()
        _cached_bytes = 0


def _expire(now: float) -> None:
    global _cached_bytes
    for key, entry in list(_cache.items()):
        if now - entry[0] > PREVIEW_CACHE_TTL_SECONDS:
            _cached_bytes -= entry[3]
            del _cache[key]


def preview_gauges() -> dict[str, int]:
    with _lock:
        _expire(time.monotonic())
        size, count = _cached_bytes, len(_cache)
    try:
        budget, flights = _runtime()
        used, pending = budget.used, len(flights)
    except RuntimeError:
        used, pending = 0, 0
    return {
        "vision_preview.cache_bytes": size,
        "vision_preview.cache_entries": count,
        "vision_preview.decode_memory_bytes": used,
        "vision_preview.inflight": pending,
        "vision_preview.memory_capacity_bytes": config.VISION_PREVIEW_MEMORY_BUDGET_MB * 1024 * 1024,
    }


def _estimate(path: Path | bytes, limits: tuple[int, int, int]) -> int:
    max_side, max_bytes, max_pixels = limits
    try:
        configure_pillow_image_limits()
        with warnings.catch_warnings():
            warnings.simplefilter("error", assistant_client.DecompressionBombWarning)
            with assistant_client.Image.open(io.BytesIO(path) if isinstance(path, bytes) else path) as image:
                if image.width * image.height > max_pixels:
                    raise ValueError("Image exceeds pixel safety limit")
                if image.format == "JPEG":
                    image.draft("RGB", (max_side, max_side))
                return image.width * image.height * 16 + max_bytes * 3
    except (OSError, SyntaxError, ValueError, assistant_client.DecompressionBombWarning,
            assistant_client.Image.DecompressionBombError) as error:
        raise assistant_client.AssistantError("Image data must be a fully decodable supported raster image", status=400) from error


async def _decode(path: Path, key: _CacheKey, generation: int, budget: _PreviewBudget) -> tuple[str, int] | None:
    global _cached_bytes
    limits = key[3:]
    weight = await run_file_operation(_estimate, path, limits, metric_name="vision_preview_probe")
    if not await budget.acquire(weight):
        return None
    try:
        # Callers shield this entire task: admission lasts until the real worker
        # completes, even if every HTTP/model caller has already been cancelled.
        metrics.increment("vision_preview.decodes")
        preview = await run_image_operation(
            assistant_client.prepare_vision_preview, path, limits=limits, metric_name="vision_preview_decode"
        )
    finally:
        await budget.release(weight)
    data_url = f"data:{preview['mime_type']};base64,{preview['b64']}"
    size = int(preview.get("bytes") or 0)
    retained = sys.getsizeof(data_url)
    with _lock:
        _expire(time.monotonic())
        if generation == _generation and retained <= PREVIEW_CACHE_MAX_BYTES:
            previous = _cache.pop(key, None)
            if previous is not None:
                _cached_bytes -= previous[3]
            _cache[key] = (time.monotonic(), data_url, size, retained)
            _cached_bytes += retained
            while len(_cache) > PREVIEW_CACHE_MAX_ENTRIES or _cached_bytes > PREVIEW_CACHE_MAX_BYTES:
                _old_key, old = _cache.popitem(last=False)
                _cached_bytes -= old[3]
    return data_url, size


async def load_preview_data_url(path: Path) -> tuple[str, int] | None:
    """Share a decode across callers; return None for strict memory-budget skips."""
    limits = (config.AI_ASSISTANT_IMAGE_MAX_SIDE, config.AI_ASSISTANT_IMAGE_MAX_BYTES, config.MAX_IMAGE_PIXELS)
    stat = await run_file_operation(path.stat, metric_name="vision_preview_stat")
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size, *limits)
    with _lock:
        _expire(time.monotonic())
        generation = _generation
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            metrics.increment("vision_preview.cache_hits")
            return hit[1], hit[2]
    budget, flights = _runtime()
    flight_key = (generation, key)
    task = flights.get(flight_key)
    if task is None:
        metrics.increment("vision_preview.cache_misses")
        task = asyncio.create_task(_decode(path, key, generation, budget))
        flights[flight_key] = task

        def finished(done: asyncio.Task) -> None:
            flights.pop(flight_key, None)
            # Retrieve failures even when all waiting callers left.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
    else:
        metrics.increment("vision_preview.shared_decodes")
    return await asyncio.shield(task)


async def prepare_preview(source: Path | bytes, **kwargs) -> dict:
    """Budget direct Assistant analysis using the same pool as Agent previews."""
    limits = (config.AI_ASSISTANT_IMAGE_MAX_SIDE, config.AI_ASSISTANT_IMAGE_MAX_BYTES, config.MAX_IMAGE_PIXELS)
    budget, _flights = _runtime()

    async def work():
        weight = await run_file_operation(_estimate, source, limits)
        if not await budget.acquire(weight):
            raise assistant_client.AssistantError("Image preview exceeds the vision decode memory budget", status=400)
        try:
            callback = (assistant_client.prepare_vision_preview_bytes if isinstance(source, bytes)
                        else assistant_client.prepare_vision_preview)
            return await run_image_operation(callback, source, limits=limits, **kwargs)
        finally:
            await budget.release(weight)

    task = asyncio.create_task(work())
    loop = asyncio.get_running_loop()
    direct = getattr(loop, "_gpt_vision_direct_tasks", None)
    if direct is None:
        direct = set()
        setattr(loop, "_gpt_vision_direct_tasks", direct)
    direct.add(task)
    task.add_done_callback(direct.discard)
    task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
    return await asyncio.shield(task)


async def drain_preview_tasks() -> None:
    """Complete real worker work before its executor and event loop shut down."""
    _budget, flights = _runtime()
    direct = getattr(asyncio.get_running_loop(), "_gpt_vision_direct_tasks", set())
    await asyncio.gather(*list(flights.values()), *list(direct), return_exceptions=True)
