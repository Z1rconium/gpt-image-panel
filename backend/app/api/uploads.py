"""Multipart upload helpers. The UploadFile surface belongs to the api layer."""

from pathlib import Path

from fastapi import UploadFile

from ..core import settings as config
from ..core.errors import DomainError, InvalidRequestError
from ..core.media import IMAGE_CONTENT_TYPE_FORMATS, image_content_type_for_filename
from ..services.uploads import IMAGE_UPLOAD_CONTENT_TYPES, IMAGE_UPLOAD_EXTENSIONS

ASSISTANT_IMAGE_UPLOAD_CHUNK_BYTES = 1024 * 1024


def resolve_upload_content_type(upload: UploadFile) -> str:
    if upload.content_type and upload.content_type.startswith("image/"):
        return upload.content_type

    return image_content_type_for_filename(upload.filename or "")


def is_image_upload(upload: UploadFile) -> bool:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in IMAGE_UPLOAD_EXTENSIONS:
        return False

    content_type = resolve_upload_content_type(upload)
    if not content_type.startswith("image/"):
        return False
    return content_type != "image/svg+xml" and content_type in IMAGE_CONTENT_TYPE_FORMATS


async def read_image_upload(
    upload: UploadFile,
    *,
    chunk_bytes: int = ASSISTANT_IMAGE_UPLOAD_CHUNK_BYTES,
) -> bytes:
    """Read an upload with the configured size cap, as a plain byte string."""
    if not is_image_upload(upload):
        raise InvalidRequestError("Upload must be a supported raster image file")

    max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024
    image_bytes = bytearray()
    while True:
        chunk = await upload.read(chunk_bytes)
        if not chunk:
            break
        if len(image_bytes) + len(chunk) > max_bytes:
            raise DomainError(
                f"Uploaded image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB",
                status_code=413,
            )
        image_bytes.extend(chunk)

    if not image_bytes:
        raise InvalidRequestError("Uploaded image is empty")

    return bytes(image_bytes)
