"""On-disk image file IO for the gallery.

Format tables, in-memory validation, and path resolution live in core/media.py
so integrations can use them without depending on this layer.
"""

import tempfile
from pathlib import Path

from ..core import settings as config
from ..core.media import (
    IMAGE_FILE_EXTENSIONS,
    Image,
    safe_image_path,
    safe_mask_path,
    validate_image_header_bytes,
    verify_pillow_image,
)


def validate_image_file_details(
    path: Path,
    *,
    filename: str = "",
    content_type: str = "",
) -> tuple[str, int, int]:
    try:
        with path.open("rb") as file:
            header = file.read(512)
    except OSError as e:
        raise ValueError("Image data could not be read") from e

    detected_format = validate_image_header_bytes(
        header,
        filename=filename,
        content_type=content_type,
    )
    width, height = verify_pillow_image(
        lambda: Image.open(path),
        expected_format=detected_format,
    )
    return detected_format, width, height


def save_image_to_temp(image_bytes: bytes, filename: str) -> Path:
    path, _format, _width, _height = save_image_to_temp_with_metadata(
        image_bytes,
        filename,
    )
    return path


def save_image_to_temp_with_metadata(
    image_bytes: bytes,
    filename: str,
) -> tuple[Path, str, int, int]:
    validate_image_header_bytes(image_bytes, filename=filename)
    path = safe_image_path(filename)
    if not path:
        raise ValueError(f"Invalid image filename: {filename}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f".{path.stem}-",
        suffix=f"{path.suffix}.tmp",
        dir=path.parent,
        delete=False,
    )
    temp_path = Path(temp_file.name)
    try:
        with temp_file:
            temp_file.write(image_bytes)
        detected_format, width, height = validate_image_file_details(
            temp_path,
            filename=filename,
        )
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path, detected_format, width, height


def promote_image_temp(filename: str, temp_path: Path) -> Path:
    path = safe_image_path(filename)
    if not path:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"Invalid image filename: {filename}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.replace(path)
    return path


def delete_image_from_disk(filename: str) -> bool:
    path = safe_image_path(filename)
    if path and path.is_file():
        path.unlink()
        return True
    return False


def write_mask_file(data: bytes, mask_filename: str) -> Path:
    """Atomically write a validated mask's bytes into MASKS_DIR.

    `data` is the already re-encoded `LA` PNG produced by
    `services.edit_masks.validate_edit_mask_file()`'s single decode
    (`EditMaskInfo.optimized_png`), so this is a plain atomic write — no
    image decode/encode happens here.
    """
    path = safe_mask_path(mask_filename)
    if not path:
        raise ValueError(f"Invalid mask filename: {mask_filename}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f".{path.stem}-",
        suffix=f"{path.suffix}.tmp",
        dir=path.parent,
        delete=False,
    )
    temp_path = Path(temp_file.name)
    try:
        with temp_file:
            temp_file.write(data)
        temp_path.replace(path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return path


def delete_mask_file(mask_filename: str) -> bool:
    path = safe_mask_path(mask_filename)
    if path and path.is_file():
        path.unlink()
        return True
    return False


def scan_image_files() -> set[str]:
    images_dir = Path(config.IMAGES_DIR)
    if not images_dir.exists():
        return set()
    return {
        path.name
        for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_FILE_EXTENSIONS
    }
