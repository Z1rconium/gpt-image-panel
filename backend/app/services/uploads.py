"""Upload constants and byte-level validation shared with the api layer."""

from ..core.errors import InvalidRequestError
from ..core.media import (
    IMAGE_EXTENSION_FORMATS,
    IMAGE_FILE_EXTENSIONS,
    IMAGE_FORMAT_CONTENT_TYPES,
    validate_image_header_bytes,
)

IMAGE_UPLOAD_EXTENSIONS = IMAGE_FILE_EXTENSIONS
IMAGE_UPLOAD_CONTENT_TYPES = {
    extension: IMAGE_FORMAT_CONTENT_TYPES[image_format]
    for extension, image_format in IMAGE_EXTENSION_FORMATS.items()
}


def validate_upload_image_bytes(image_bytes: bytes, filename: str, content_type: str) -> str:
    try:
        return validate_image_header_bytes(
            image_bytes,
            filename=filename,
            content_type=content_type,
        )
    except ValueError as e:
        raise InvalidRequestError(str(e)) from e
