"""Admission-time validation for /v1/images/edits mask uploads.

The upstream contract is strict: a mask must be a PNG with an alpha channel,
smaller than 4 MB, with the same dimensions as the first (primary) image, and
its fully transparent pixels (alpha == 0) mark the region to edit. Everything
here runs before a job is queued so bad masks fail fast instead of after
admission.
"""

from dataclasses import dataclass
from pathlib import Path

from ..core.media import Image, validate_image_header_bytes, verified_pillow_image
from ..repositories.image_files import validate_image_file_details

MASK_ALPHA_MODES = {"RGBA", "LA", "PA"}
MASK_SNIFF_BYTES = 512


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
    """Validate a mask PNG with a single decode.

    Everything the upstream contract cares about (alpha channel, dimensions,
    fully-transparent ratio) is read off the one decoded `image` object;
    `getchannel("A")` is used directly for modes that already carry an alpha
    band (RGBA/LA/PA) and only falls back to a full `convert("RGBA")` copy for
    a palette image whose transparency comes from a tRNS chunk.
    """
    try:
        with path.open("rb") as file:
            header = file.read(MASK_SNIFF_BYTES)
    except OSError as e:
        raise ValueError("Mask data could not be read") from e

    detected_format = validate_image_header_bytes(
        header,
        filename="mask.png",
        content_type="image/png",
    )

    with verified_pillow_image(
        lambda: Image.open(path),
        expected_format=detected_format,
    ) as image:
        if image.mode not in MASK_ALPHA_MODES and "transparency" not in image.info:
            raise ValueError("Mask must be a PNG file with an alpha channel")
        width, height = image.size
        if (width, height) != (expected_width, expected_height):
            raise ValueError(
                "Mask dimensions must match the primary image: "
                f"mask is {width}x{height}, image is {expected_width}x{expected_height}"
            )
        if image.mode in MASK_ALPHA_MODES:
            zeros = image.getchannel("A").histogram()[0]
        else:
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
    *,
    primary_width: int,
    primary_height: int,
) -> EditMaskInfo:
    return validate_edit_mask_file(
        mask_path,
        expected_width=primary_width,
        expected_height=primary_height,
    )


def validate_edit_mask_against_primary_path(
    mask_path: Path,
    primary_path: Path,
    *,
    primary_filename: str = "",
    primary_content_type: str = "",
) -> EditMaskInfo:
    """Thin wrapper kept for callers that only have the primary's file path.

    Decodes the primary a second time to recover its size; the hot path in
    `api/routers/edits.py` avoids this by reusing the size already captured
    when the primary was admitted (see `EditImageSource.width/height`).
    """
    _format, width, height = validate_image_file_details(
        primary_path,
        filename=primary_filename,
        content_type=primary_content_type,
    )
    return validate_edit_mask_against_primary(
        mask_path,
        primary_width=width,
        primary_height=height,
    )
