"""Composite a masked edit onto its primary image before gallery persistence."""

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat, UnidentifiedImageError

from ...core import settings as config


@dataclass(frozen=True)
class PasteBackOutcome:
    image_bytes: bytes
    status: str
    metadata: dict[str, str | float] = field(default_factory=dict)


def _skipped(image_bytes: bytes, reason: str) -> PasteBackOutcome:
    status = f"skipped:{reason}"
    return PasteBackOutcome(image_bytes, status, {"paste_back": status})


def paste_back_image(
    result_bytes: bytes,
    primary_path: Path,
    mask_path: Path,
    *,
    output_format: str | None = None,
    output_compression: int | None = None,
    background: str = "auto",
) -> PasteBackOutcome:
    """Keep the painted region from the model and restore untouched primary pixels.

    A narrow feather is placed entirely *outside* the transparent mask. The
    drift guard avoids a visible seam when the model changed the kept scene.
    Every skip returns the upstream bytes unchanged.
    """
    try:
        with Image.open(primary_path) as source, Image.open(mask_path) as mask_source, Image.open(BytesIO(result_bytes)) as result_source:
            source.load()
            mask_source.load()
            result_source.load()
            if source.size != mask_source.size:
                return _skipped(result_bytes, "decode")
            original_format = (result_source.format or output_format or "png").lower()
            icc_profile = source.info.get("icc_profile")
            primary = source.copy()
            mask = mask_source.getchannel("A")
            result = result_source.copy()
    except (OSError, ValueError, UnidentifiedImageError):
        return _skipped(result_bytes, "decode")

    source_ratio = primary.width / primary.height
    result_ratio = result.width / result.height
    if abs(result_ratio / source_ratio - 1) > 0.01:
        return _skipped(result_bytes, "aspect_mismatch")

    scale = primary.width / result.width
    if result.size != primary.size:
        result = result.resize(primary.size, Image.Resampling.LANCZOS)

    # Upstream edits exactly the fully transparent mask pixels. Point() makes
    # this binary even when an API client supplied soft alpha values.
    edited = mask.point(lambda value: 255 if value == 0 else 0)
    band = max(4, round(0.008 * min(primary.size)))
    feather = edited.filter(ImageFilter.GaussianBlur(band / 2)).point(
        lambda value: min(255, value * 2)
    )
    composite_mask = ImageChops.lighter(edited, feather)

    # Ignore the edit and its surrounding seam when comparing preserved space.
    # Downsample first so the dilation and MAD have bounded cost on 4K images.
    small_size = (
        max(1, round(primary.width * min(1, 512 / max(primary.size)))),
        max(1, round(primary.height * min(1, 512 / max(primary.size)))),
    )
    small_edited = edited.resize(small_size, Image.Resampling.NEAREST)
    radius = max(1, round(2 * band * small_size[0] / primary.width))
    excluded = small_edited.filter(ImageFilter.MaxFilter(2 * radius + 1))
    kept = ImageChops.invert(excluded)
    kept_fraction = ImageStat.Stat(kept).mean[0] / 255
    if kept_fraction >= 0.02:
        small_primary = primary.convert("L").resize(small_size, Image.Resampling.BILINEAR)
        small_result = result.convert("L").resize(small_size, Image.Resampling.BILINEAR)
        drift = ImageStat.Stat(ImageChops.difference(small_primary, small_result), kept).mean[0]
        if drift > 12:
            return _skipped(result_bytes, "keep_region_changed")

    has_alpha = (
        "A" in primary.getbands()
        or "A" in result.getbands()
        or background == "transparent"
    )
    mode = "RGBA" if has_alpha else "RGB"
    composed = Image.composite(result.convert(mode), primary.convert(mode), composite_mask)
    image_format = original_format if original_format in {"png", "jpeg", "webp"} else "png"
    if image_format == "jpeg" and has_alpha:
        image_format = "png"
    save_options: dict[str, object] = {}
    if image_format == "png":
        save_options["compress_level"] = 6
    else:
        save_options["quality"] = min(output_compression or 95, 95)
    if icc_profile:
        save_options["icc_profile"] = icc_profile
    output = BytesIO()
    composed.save(output, format=image_format.upper(), **save_options)
    image_bytes = output.getvalue()
    if len(image_bytes) > config.MAX_FILE_SIZE_MB * 1024 * 1024:
        return _skipped(result_bytes, "too_large")
    return PasteBackOutcome(
        image_bytes,
        "applied",
        {"paste_back": "applied", "paste_back_scale": round(scale, 3)},
    )
