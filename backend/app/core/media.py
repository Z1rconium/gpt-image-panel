"""Image format tables, in-memory validation, and gallery path resolution.

Pure helpers: everything here works on bytes, filenames, or configured
directories, so repositories and integrations can share it without either layer
depending on the other. On-disk read/write lives in repositories/image_files.py.
"""

import contextlib
import io
import mimetypes
import struct
import uuid
import warnings
from pathlib import Path

from . import settings as config

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover - Pillow is a runtime dependency
    Image = None
    ImageOps = None
    UnidentifiedImageError = OSError

IMAGE_FILE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

IMAGE_EXTENSION_FORMATS = {
    ".avif": "avif",
    ".bmp": "bmp",
    ".gif": "gif",
    ".heic": "heif",
    ".heif": "heif",
    ".ico": "ico",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".webp": "webp",
}
IMAGE_CONTENT_TYPE_FORMATS = {
    "image/avif": "avif",
    "image/bmp": "bmp",
    "image/gif": "gif",
    "image/heic": "heif",
    "image/heif": "heif",
    "image/ico": "ico",
    "image/icon": "ico",
    "image/jpeg": "jpeg",
    "image/pjpeg": "jpeg",
    "image/png": "png",
    "image/tiff": "tiff",
    "image/vnd.microsoft.icon": "ico",
    "image/webp": "webp",
    "image/x-icon": "ico",
}
IMAGE_FORMAT_CONTENT_TYPES = {
    "avif": "image/avif",
    "bmp": "image/bmp",
    "gif": "image/gif",
    "heif": "image/heif",
    "ico": "image/x-icon",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "tiff": "image/tiff",
    "webp": "image/webp",
}
THUMBNAIL_EXTENSION = ".webp"
THUMBNAIL_CONTENT_TYPE = "image/webp"
MASK_EXTENSION = ".png"
MASK_CONTENT_TYPE = "image/png"
PILLOW_FORMATS = {
    "AVIF": "avif",
    "BMP": "bmp",
    "GIF": "gif",
    "HEIF": "heif",
    "ICO": "ico",
    "JPEG": "jpeg",
    "JPG": "jpeg",
    "PNG": "png",
    "TIFF": "tiff",
    "WEBP": "webp",
}

# EXIF tag 274 and the formats whose pixels survive a rotation re-encode
# without losing anything the upstream contract depends on.
EXIF_ORIENTATION_TAG = 274
ORIENTATION_REWRITE_FORMATS = {"jpeg", "png", "tiff", "webp"}
# Orientations 5-8 describe a quarter turn, which swaps the two dimensions.
ORIENTATION_SWAPS_AXES = {5, 6, 7, 8}


def image_content_type_for_filename(filename: str | Path) -> str:
    """Return the repository's stable image MIME type for a filename."""

    name = str(filename or "")
    image_format = IMAGE_EXTENSION_FORMATS.get(Path(name).suffix.lower())
    if image_format:
        return IMAGE_FORMAT_CONTENT_TYPES[image_format]
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def configure_pillow_image_limits() -> None:
    if Image is not None:
        Image.MAX_IMAGE_PIXELS = config.MAX_IMAGE_PIXELS


configure_pillow_image_limits()


def generate_image_id() -> str:
    return str(uuid.uuid4())


def detect_image_format(image_bytes: bytes) -> str | None:
    stripped = image_bytes[:512].lstrip().lower()
    if stripped.startswith((b"<svg", b"<?xml", b"<!doctype html", b"<html")):
        return None
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "jpeg"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if image_bytes.startswith(b"RIFF") and len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP":
        return "webp"
    if image_bytes.startswith(b"BM"):
        return "bmp"
    if image_bytes.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if len(image_bytes) >= 12 and image_bytes[4:8] == b"ftyp":
        brand = image_bytes[8:12]
        compatible = image_bytes[8:32]
        if brand in {b"avif", b"avis"} or b"avif" in compatible or b"avis" in compatible:
            return "avif"
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "heif"
    if image_bytes.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    return None


def validate_image_header_bytes(
    image_bytes: bytes,
    *,
    filename: str = "",
    content_type: str = "",
) -> str:
    detected_format = detect_image_format(image_bytes)
    if not detected_format:
        raise ValueError("Image data must be a supported raster image format")

    suffix = Path(filename or "").suffix.lower()
    extension_format = IMAGE_EXTENSION_FORMATS.get(suffix)
    if suffix and extension_format != detected_format:
        raise ValueError("Image file extension does not match image data")

    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    content_type_format = IMAGE_CONTENT_TYPE_FORMATS.get(normalized_content_type)
    if normalized_content_type and content_type_format != detected_format:
        raise ValueError("Image content type does not match image data")

    return detected_format


def _pillow_format_key(format_name: str | None) -> str | None:
    if not format_name:
        return None
    return PILLOW_FORMATS.get(format_name.upper(), format_name.lower())


def _raise_image_validation_error(error: BaseException) -> None:
    raise ValueError(
        "Image data must be a fully decodable supported raster image"
    ) from error


def _decompression_bomb_warning():
    if Image is None:
        return Warning
    return getattr(Image, "DecompressionBombWarning", Warning)


@contextlib.contextmanager
def verified_pillow_image(opener, *, expected_format: str):
    """Open, fully decode, and format-check a Pillow image; yield it still open.

    Shares the decompression-bomb and format-mismatch guards with
    `verify_pillow_image()` so callers that need to inspect the decoded image
    (e.g. mask alpha channel analysis) don't have to decode it a second time.
    Only the open/decode step runs under those guards — an exception raised by
    the caller's own code after the yield propagates unchanged, instead of
    being rewritten into the generic decode-failure message below.
    """
    if Image is None:
        raise ValueError("Pillow is required to validate image data")

    configure_pillow_image_limits()
    warning_type = _decompression_bomb_warning()
    bomb_error_type = getattr(Image, "DecompressionBombError", OSError)
    with contextlib.ExitStack() as stack:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", warning_type)
                image = stack.enter_context(opener())
                decoded_format = _pillow_format_key(getattr(image, "format", None))
                if decoded_format and decoded_format != expected_format:
                    raise ValueError("Image decoder format does not match image data")
                if getattr(image, "is_animated", False):
                    image.seek(0)
                image.load()
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise ValueError("Image dimensions must be positive")
        except warning_type as e:
            _raise_image_validation_error(e)
        except (OSError, UnidentifiedImageError, SyntaxError, ValueError, bomb_error_type) as e:
            _raise_image_validation_error(e)

        yield image


def verify_pillow_image(
    opener,
    *,
    expected_format: str,
) -> tuple[int, int]:
    with verified_pillow_image(opener, expected_format=expected_format) as image:
        width, height = image.size
        return int(width), int(height)


def pillow_exif_orientation(image) -> int:
    """EXIF orientation of an already-decoded Pillow image.

    Returns 1 (upright) when the tag is absent, out of range, or the container's
    EXIF block cannot be parsed at all — every caller treats anything but a real
    rotation as "leave these pixels alone".
    """

    try:
        exif = image.getexif()
    except Exception:
        return 1
    if not exif:
        return 1
    try:
        value = exif.get(EXIF_ORIENTATION_TAG)
    except Exception:
        return 1
    if isinstance(value, int) and 2 <= value <= 8:
        return value
    return 1


def orientation_upright_size(width: int, height: int, orientation: int) -> tuple[int, int]:
    """Dimensions an image with `orientation` has once it is displayed upright."""

    if orientation in ORIENTATION_SWAPS_AXES:
        return height, width
    return width, height


def encode_upright_image(image, *, image_format: str) -> bytes:
    """Rotate a decoded image upright and re-encode it in `image_format`.

    `ImageOps.exif_transpose` also drops the orientation tag, so the re-encoded
    file cannot be rotated a second time by anything downstream. JPEG keeps
    quality 95 and the ICC profile; PNG/TIFF/WebP stay in their lossless mode,
    since the rotation itself never needed to be lossy.
    """

    if ImageOps is None:  # pragma: no cover - Pillow is a runtime dependency
        raise ValueError("Pillow is required to normalize image orientation")
    upright = ImageOps.exif_transpose(image)
    if upright is None:  # pragma: no cover - exif_transpose never returns None for a loaded image
        upright = image

    options: dict = {"format": image_format.upper()}
    icc_profile = getattr(image, "info", {}).get("icc_profile")
    if icc_profile:
        options["icc_profile"] = icc_profile
    if image_format == "jpeg":
        options["quality"] = 95
    elif image_format == "webp":
        options["lossless"] = True

    buffer = io.BytesIO()
    upright.save(buffer, **options)
    return buffer.getvalue()


def validate_image_bytes(
    image_bytes: bytes,
    *,
    filename: str = "",
    content_type: str = "",
) -> str:
    detected_format = validate_image_header_bytes(
        image_bytes,
        filename=filename,
        content_type=content_type,
    )
    verify_pillow_image(
        lambda: Image.open(io.BytesIO(image_bytes)),
        expected_format=detected_format,
    )
    return detected_format


def get_image_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) >= 24:
        return struct.unpack(">II", image_bytes[16:24])

    if image_bytes.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(image_bytes):
            if image_bytes[offset] != 0xFF:
                offset += 1
                continue
            marker = image_bytes[offset + 1]
            offset += 2
            while marker == 0xFF and offset < len(image_bytes):
                marker = image_bytes[offset]
                offset += 1
            if marker in (0xD8, 0xD9):
                continue
            if offset + 2 > len(image_bytes):
                return None
            segment_length = struct.unpack(">H", image_bytes[offset : offset + 2])[0]
            if segment_length < 2 or offset + segment_length > len(image_bytes):
                return None
            if marker in (
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            ):
                height, width = struct.unpack(">HH", image_bytes[offset + 3 : offset + 7])
                return width, height
            offset += segment_length

    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        chunk_type = image_bytes[12:16]
        if chunk_type == b"VP8X" and len(image_bytes) >= 30:
            width = int.from_bytes(image_bytes[24:27], "little") + 1
            height = int.from_bytes(image_bytes[27:30], "little") + 1
            return width, height
        if chunk_type == b"VP8 " and len(image_bytes) >= 30:
            width, height = struct.unpack("<HH", image_bytes[26:30])
            return width & 0x3FFF, height & 0x3FFF
        if chunk_type == b"VP8L" and len(image_bytes) >= 25 and image_bytes[20] == 0x2F:
            bits = int.from_bytes(image_bytes[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return width, height

    return None


def image_dimension_metadata(image_bytes: bytes) -> dict[str, int]:
    dimensions = get_image_dimensions(image_bytes)
    if not dimensions and Image is not None:
        configure_pillow_image_limits()
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                dimensions = image.size
        except (OSError, UnidentifiedImageError, SyntaxError, ValueError):
            dimensions = None
    if not dimensions:
        return {}
    width, height = dimensions
    return {"image_width": width, "image_height": height}


def _safe_path(filename: str, base_dir: str, allowed_suffixes: set[str]) -> Path | None:
    if not filename or "\x00" in filename or "/" in filename or "\\" in filename:
        return None
    if filename in {".", ".."}:
        return None

    path_name = Path(filename)
    if path_name.name != filename or path_name.suffix.lower() not in allowed_suffixes:
        return None

    root = Path(base_dir).resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def safe_image_path(filename: str) -> Path | None:
    return _safe_path(filename, config.IMAGES_DIR, IMAGE_FILE_EXTENSIONS)


def safe_thumbnail_path(filename: str) -> Path | None:
    return _safe_path(filename, config.THUMBNAILS_DIR, {THUMBNAIL_EXTENSION})


def safe_mask_path(filename: str) -> Path | None:
    return _safe_path(filename, config.MASKS_DIR, {MASK_EXTENSION})
