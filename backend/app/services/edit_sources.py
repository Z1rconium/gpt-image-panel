"""Edit-source staging shared by the edit routes and the agent tools."""

import hashlib
import os
import tempfile
from pathlib import Path

from ..core import settings as config
from ..core.errors import DomainError, InvalidRequestError, NotFoundError
from ..core.media import image_content_type_for_filename, safe_image_path
from ..repositories.gallery.queries import get_gallery_entry
from ..repositories.image_files import validate_and_normalize_image_file
from ..runtime.blocking import run_db_operation, run_image_operation
from .gallery_archive_shared import max_upload_bytes
from .job_queue import EditImageSource
from .uploads import validate_upload_image_bytes

EDIT_SOURCE_SNIFF_BYTES = 512
EDIT_SOURCE_CHUNK_BYTES = 1024 * 1024


def create_edit_source_temp_path(filename: str) -> tuple[int, Path]:
    suffix = Path(filename or "").suffix.lower() or ".img"
    temp_dir = Path(config.DATA_DIR) / "edit-sources"
    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="edit-source-",
        suffix=suffix,
        dir=temp_dir,
    )
    return fd, Path(temp_name)


def validate_edit_source_header(
    image_header: bytes,
    byte_size: int,
    filename: str,
    content_type: str,
    *,
    empty_detail: str,
    too_large_detail: str,
    max_bytes: int | None = None,
):
    if byte_size == 0:
        raise InvalidRequestError(empty_detail)
    limit = max_upload_bytes() if max_bytes is None else max_bytes
    if byte_size > limit:
        raise InvalidRequestError(too_large_detail)
    validate_upload_image_bytes(image_header, filename, content_type)


def validate_edit_source_file_details(
    path: Path,
    filename: str,
    content_type: str,
    *,
    orientation_mask_size: tuple[int, int] | None = None,
) -> tuple[int, int, int]:
    """Full Pillow decode, returning the size and byte size so callers don't decode again.

    A photo that carries an EXIF orientation is rotated upright and re-encoded
    in the same decode (see `validate_and_normalize_image_file`), because the
    browser shows and the mask editor draws the rotated pixels while Pillow
    reports the raw ones — a mismatch that rejects non-square phone photos with
    a 422 and silently edits the wrong region on square ones.

    Used for primary/reference images; mask uploads skip this (see
    `copy_edit_source_stream_to_temp(..., validate=False)`) because
    `validate_edit_mask_file()` decodes them once on its own.
    """
    try:
        _format, width, height, byte_size = validate_and_normalize_image_file(
            path,
            filename=filename,
            content_type=content_type,
            orientation_mask_size=orientation_mask_size,
        )
        return width, height, byte_size
    except ValueError as e:
        raise InvalidRequestError(str(e)) from e


def copy_edit_source_file_to_temp(
    path: Path,
    filename: str,
    content_type: str,
    *,
    empty_detail: str,
    too_large_detail: str,
    read_error_detail: str,
    max_bytes: int | None = None,
    orientation_mask_size: tuple[int, int] | None = None,
    gallery_image_id: str | None = None,
) -> EditImageSource:
    try:
        with path.open("rb") as source:
            return copy_edit_source_stream_to_temp(
                source,
                filename,
                content_type,
                empty_detail=empty_detail,
                too_large_detail=too_large_detail,
                read_error_detail=read_error_detail,
                max_bytes=max_bytes,
                orientation_mask_size=orientation_mask_size,
                gallery_image_id=gallery_image_id,
            )
    except DomainError:
        raise
    except OSError as e:
        raise DomainError(read_error_detail, status_code=500) from e


def copy_edit_source_stream_to_temp(
    source,
    filename: str,
    content_type: str,
    *,
    empty_detail: str,
    too_large_detail: str,
    read_error_detail: str,
    max_bytes: int | None = None,
    validate: bool = True,
    orientation_mask_size: tuple[int, int] | None = None,
    gallery_image_id: str | None = None,
) -> EditImageSource:
    """Copy an upload to a temp edit-source file.

    `validate=False` skips the full Pillow decode (still does the cheap magic-
    byte header sniff) for uploads that will be fully decoded once, later, by
    a caller-specific validator — currently only mask uploads, whose
    alpha/dimension checks in `validate_edit_mask_file()` already decode the
    file. Width/height stay 0 when the decode is skipped.

    `byte_size` is the size of the file as it ends up on disk, so an EXIF
    orientation re-encode (which changes the byte count) still reserves the
    right amount of pending-upload memory.
    """
    limit = max_upload_bytes() if max_bytes is None else max_bytes
    fd, temp_path = create_edit_source_temp_path(filename)
    total = 0
    header = bytearray()
    digest = hashlib.sha256()

    try:
        source.seek(0)
        with os.fdopen(fd, "wb") as target:
            while True:
                chunk = source.read(EDIT_SOURCE_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise InvalidRequestError(too_large_detail)
                if len(header) < EDIT_SOURCE_SNIFF_BYTES:
                    header.extend(chunk[: EDIT_SOURCE_SNIFF_BYTES - len(header)])
                digest.update(chunk)
                target.write(chunk)
    except DomainError:
        temp_path.unlink(missing_ok=True)
        raise
    except OSError as e:
        temp_path.unlink(missing_ok=True)
        raise DomainError(read_error_detail, status_code=500) from e
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    width = height = 0
    try:
        validate_edit_source_header(
            bytes(header),
            total,
            filename,
            content_type,
            empty_detail=empty_detail,
            too_large_detail=too_large_detail,
            max_bytes=max_bytes,
        )
        if validate:
            width, height, total = validate_edit_source_file_details(
                temp_path,
                filename,
                content_type,
                orientation_mask_size=orientation_mask_size,
            )
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return EditImageSource(
        temp_path, total, filename, content_type, width=width, height=height,
        raw_sha256=digest.hexdigest(), gallery_image_id=gallery_image_id,
    )


def cleanup_edit_sources(sources: list[EditImageSource]):
    for source in sources:
        source.temp_path.unlink(missing_ok=True)


async def read_gallery_edit_source(
    image_id: str,
    *,
    orientation_mask_size: tuple[int, int] | None = None,
) -> EditImageSource:
    entry = await run_db_operation(
        get_gallery_entry,
        image_id,
        metric_name="get_gallery_edit_source",
    )
    if not entry:
        raise NotFoundError("Gallery entry not found")

    path = safe_image_path(entry.filename)
    if not path or not path.exists():
        raise NotFoundError("Gallery image file not found")

    image_content_type = image_content_type_for_filename(path.name)

    return await run_image_operation(
        copy_edit_source_file_to_temp,
        path,
        path.name,
        image_content_type,
        empty_detail="Gallery image is empty",
        too_large_detail=f"Gallery image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB.",
        read_error_detail="Failed to read gallery image",
        metric_name="copy_validate_gallery_edit_source",
        orientation_mask_size=orientation_mask_size,
        gallery_image_id=image_id,
    )
