"""Admission-time validation for /v1/images/edits mask uploads.

The upstream contract is strict: a mask must be a PNG with an alpha channel,
smaller than 4 MB, with the same dimensions as the first (primary) image, and
its fully transparent pixels (alpha == 0) mark the region to edit. Everything
here runs before a job is queued so bad masks fail fast instead of after
admission.
"""

from dataclasses import dataclass
from pathlib import Path

from ..core.media import Image, configure_pillow_image_limits
from ..repositories.image_files import (
    validate_image_file,
    validate_image_file_details,
)

MASK_ALPHA_MODES = {"RGBA", "LA", "PA"}


@dataclass(frozen=True)
class EditMaskInfo:
    width: int
    height: int
    transparent_ratio: float


def validate_edit_mask_file(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
) -> EditMaskInfo:
    validate_image_file(path, filename="mask.png", content_type="image/png")

    configure_pillow_image_limits()
    with Image.open(path) as image:
        if image.mode not in MASK_ALPHA_MODES and "transparency" not in image.info:
            raise ValueError("Mask must be a PNG file with an alpha channel")
        width, height = image.size
        if (width, height) != (expected_width, expected_height):
            raise ValueError(
                "Mask dimensions must match the primary image: "
                f"mask is {width}x{height}, image is {expected_width}x{expected_height}"
            )
        zeros = image.convert("RGBA").getchannel("A").histogram()[0]

    total = width * height
    transparent_ratio = zeros / total if total else 0.0
    if transparent_ratio <= 0:
        raise ValueError("Mask has no fully transparent region to edit")
    return EditMaskInfo(
        width=width,
        height=height,
        transparent_ratio=transparent_ratio,
    )


def validate_edit_mask_against_primary(
    mask_path: Path,
    primary_path: Path,
    *,
    primary_filename: str = "",
    primary_content_type: str = "",
) -> EditMaskInfo:
    _format, width, height = validate_image_file_details(
        primary_path,
        filename=primary_filename,
        content_type=primary_content_type,
    )
    return validate_edit_mask_file(
        mask_path,
        expected_width=width,
        expected_height=height,
    )
