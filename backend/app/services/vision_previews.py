"""Bounded in-process cache for assistant vision previews of gallery images.

Gallery filenames embed the image id and files are written once, so a decoded
preview can be reused across agent rounds and turns; the stat key still guards
against a file being replaced or removed in place.
"""

import threading
import time
from collections import OrderedDict
from pathlib import Path

from ..integrations import assistant_client

PREVIEW_CACHE_MAX_ENTRIES = 32
PREVIEW_CACHE_MAX_BYTES = 24 * 1024 * 1024
PREVIEW_CACHE_TTL_SECONDS = 15 * 60.0

_CacheKey = tuple[str, int, int]
# key -> (stored_at, data_url, preview_bytes)
_cache: "OrderedDict[_CacheKey, tuple[float, str, int]]" = OrderedDict()
_cached_bytes = 0
_lock = threading.Lock()


def clear_preview_cache() -> None:
    global _cached_bytes
    with _lock:
        _cache.clear()
        _cached_bytes = 0


def load_preview_data_url(path: Path) -> tuple[str, int]:
    """Return ``(data_url, preview_bytes)`` for a gallery file, cached by path state."""
    global _cached_bytes
    stat = path.stat()
    key: _CacheKey = (str(path), stat.st_mtime_ns, stat.st_size)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            if now - hit[0] <= PREVIEW_CACHE_TTL_SECONDS:
                _cache.move_to_end(key)
                return hit[1], hit[2]
            del _cache[key]
            _cached_bytes -= hit[2]

    preview = assistant_client.prepare_vision_preview(path)
    data_url = f"data:{preview['mime_type']};base64,{preview['b64']}"
    size = int(preview.get("bytes") or 0)
    with _lock:
        previous = _cache.pop(key, None)
        if previous is not None:
            _cached_bytes -= previous[2]
        _cache[key] = (now, data_url, size)
        _cached_bytes += size
        while len(_cache) > PREVIEW_CACHE_MAX_ENTRIES or (
            _cached_bytes > PREVIEW_CACHE_MAX_BYTES and len(_cache) > 1
        ):
            _old_key, (_stored_at, _old_url, old_size) = _cache.popitem(last=False)
            _cached_bytes -= old_size
    return data_url, size
