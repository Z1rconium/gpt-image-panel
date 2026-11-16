"""Static SVG validation shared by metadata and generated output admission."""

from collections import OrderedDict
import hashlib
import math
import re
import threading

from defusedxml import ElementTree

from . import settings as config

MAX_SVG_NODES = 200_000
_SVG_CACHE_MAX_ENTRIES = 64
_FORBIDDEN_TAGS = {
    "script",
    "foreignobject",
    "image",
    "animate",
    "animatetransform",
    "animatemotion",
    "set",
    "audio",
    "video",
}
_UNSAFE_CSS_RE = re.compile(r"@import|@font-face|\\", re.I)
# Reject any url(...) unless it immediately opens a local #fragment without nested parens,
# avoiding quadratic backtracking on inputs with many unterminated "url(" tokens.
_EXTERNAL_URL_RE = re.compile(r"url\s*\((?!\s*(?:['\"]\s*)?#[^()]*\))", re.I)
_DIMENSION_RE = re.compile(r"([\d.+eE-]+)(?:px)?")
_VIEWBOX_SPLIT_RE = re.compile(r"[\s,]+")



class _TransientSvgError(ValueError):
    """Resource exhaustion while parsing; must not be cached as a verdict."""


_SVG_CACHE_LOCK = threading.Lock()
_SVG_CACHE: OrderedDict[tuple[int, bytes, int], tuple[int, int] | None] = OrderedDict()


def _parse_svg_uncached(data: bytes) -> tuple[int, int]:
    try:
        root = ElementTree.fromstring(
            data,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
        if root.tag != "{http://www.w3.org/2000/svg}svg" and root.tag != "svg":
            raise ValueError("Expected SVG root")
        for node_count, node in enumerate(root.iter(), start=1):
            if node_count > MAX_SVG_NODES:
                raise ValueError("SVG node count exceeds limit")
            tag = node.tag.split("}")[-1].lower()
            if tag in _FORBIDDEN_TAGS:
                raise ValueError("Active SVG content is forbidden")
            for key, value in node.attrib.items():
                name = key.split("}")[-1].lower()
                if name.startswith("on") or (
                    name in {"href", "src"} and not value.strip().startswith("#")
                ):
                    raise ValueError("External or active SVG reference")
            attr_text = " ".join(node.attrib.values())
            if attr_text and node.text:
                text = f"{attr_text} {node.text}"
            else:
                text = attr_text or (node.text or "")
            if _UNSAFE_CSS_RE.search(text):
                raise ValueError("Unsafe SVG CSS")
            if _EXTERNAL_URL_RE.search(text):
                raise ValueError("External SVG URL")
        view = [
            float(v)
            for v in _VIEWBOX_SPLIT_RE.split(root.get("viewBox", "").strip())
            if v
        ]

        def dimension(name: str, index: int) -> float:
            value = root.get(name, "")
            match = _DIMENSION_RE.fullmatch(value)
            return float(match[1]) if match else (view[index] if len(view) == 4 else 0)

        width, height = dimension("width", 2), dimension("height", 3)
        if (
            not all(math.isfinite(v) and v > 0 for v in (width, height))
            or width * height > config.MAX_IMAGE_PIXELS
        ):
            raise ValueError("SVG dimensions exceed image limits")
        width_px, height_px = math.ceil(width), math.ceil(height)
        if (
            max(width_px, height_px) > min(config.MAX_IMAGE_PIXELS, 2**31 - 1)
            or width_px * height_px > config.MAX_IMAGE_PIXELS
        ):
            raise ValueError("SVG raster dimensions exceed image limits")
        return width_px, height_px
    except (MemoryError, RecursionError) as exc:
        raise _TransientSvgError("Invalid or unsafe SVG image") from exc
    except Exception as exc:
        raise ValueError("Invalid or unsafe SVG image") from exc


def _store_svg_cache(
    key: tuple[int, bytes, int],
    value: tuple[int, int] | None,
) -> None:
    with _SVG_CACHE_LOCK:
        _SVG_CACHE[key] = value
        _SVG_CACHE.move_to_end(key)
        if len(_SVG_CACHE) > _SVG_CACHE_MAX_ENTRIES:
            _SVG_CACHE.popitem(last=False)


def validate_svg_bytes(data: bytes) -> tuple[int, int]:
    """Validate static SVG XML/CSS bounds and return rounded `(width, height)`."""
    max_bytes = int(config.MAX_FILE_SIZE_MB) * 1024 * 1024
    if len(data) > max_bytes:
        raise ValueError("SVG exceeds byte limit")

    key = (len(data), hashlib.sha256(data).digest(), int(config.MAX_IMAGE_PIXELS))
    with _SVG_CACHE_LOCK:
        if key in _SVG_CACHE:
            _SVG_CACHE.move_to_end(key)
            cached = _SVG_CACHE[key]
            if cached is None:
                raise ValueError("Invalid or unsafe SVG image")
            return cached

    try:
        dimensions = _parse_svg_uncached(data)
    except _TransientSvgError:
        raise
    except ValueError:
        _store_svg_cache(key, None)
        raise

    _store_svg_cache(key, dimensions)
    return dimensions
