"""Validation boundary for generated originals; edit inputs remain raster-only."""
import io
from . import media
from .svg_validation import _svg



def detect_generated_image_format(data: bytes) -> str | None:
    raster = media.detect_image_format(data)
    if raster:
        return raster
    try:
        _svg(data)
        return 'svg'
    except ValueError:
        return None


def validate_generated_image_header_bytes(data: bytes, *, filename: str = '', content_type: str = '') -> str:
    if media.detect_image_format(data):
        return media.validate_image_header_bytes(data, filename=filename, content_type=content_type)
    _svg(data)
    if filename and not filename.lower().endswith('.svg'):
        raise ValueError('Image extension mismatch')
    if content_type and content_type.split(';')[0].strip().lower() != 'image/svg+xml':
        raise ValueError('Image content type mismatch')
    return 'svg'


def get_generated_image_dimensions(data: bytes) -> tuple[int, int] | None:
    if not media.detect_image_format(data):
        return _svg(data)
    dimensions = media.get_image_dimensions(data)
    if dimensions:
        return dimensions
    with media.Image.open(io.BytesIO(data)) as image:
        return image.size


def validate_generated_image_bytes(data: bytes, *, filename: str = '', content_type: str = '') -> str:
    detected = validate_generated_image_header_bytes(data, filename=filename, content_type=content_type)
    if detected != 'svg':
        media.validate_image_bytes(data, filename=filename, content_type=content_type)
    return detected
