"""Composite a masked edit onto its primary image before gallery persistence."""

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat, UnidentifiedImageError

from ...core import settings as config

# Feather band width as a fraction of the smaller primary side, with a floor
# so small images still get a visible blend.
FEATHER_BAND_FRACTION = 0.008
FEATHER_BAND_MIN_PX = 4
# Multiplier on the blurred band so the feather reaches full opacity at the
# edited-region boundary instead of peaking at 50%.
FEATHER_GAIN = 2
# Drift-guard sampling runs on a bounded downscale of primary and result.
DRIFT_SAMPLE_MAX_PX = 512
# Dilation radius of the excluded (edited) zone, in units of the feather band.
DRIFT_EXCLUDE_BANDS = 2
# The guard only runs when at least this fraction of the frame is kept space;
# smaller samples are too little to judge drift on.
DRIFT_MIN_KEPT_FRACTION = 0.02
# Mean absolute per-channel difference (0-255) above which the model is
# judged to have altered the preserved scene. A JPEG result with strong
# chroma artifacts may trip this more often than the old luma-only guard;
# a skip is safe and observable via the paste_back metadata.
DRIFT_THRESHOLD = 12


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
    drift guard compares red, green, and blue separately (max of the three
    channel means), so a color shift that leaves luminance unchanged is
    caught too. Every skip returns the upstream bytes unchanged.
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
    band = max(FEATHER_BAND_MIN_PX, round(FEATHER_BAND_FRACTION * min(primary.size)))
    feather = edited.filter(ImageFilter.GaussianBlur(band / 2)).point(
        lambda value: min(255, value * FEATHER_GAIN)
    )
    composite_mask = ImageChops.lighter(edited, feather)

    # Ignore the edit and its surrounding seam when comparing preserved space.
    # Downsample first so the dilation and MAD have bounded cost on 4K images.
    sample_scale = min(1, DRIFT_SAMPLE_MAX_PX / max(primary.size))
    small_size = (
        max(1, round(primary.width * sample_scale)),
        max(1, round(primary.height * sample_scale)),
    )
    small_edited = edited.resize(small_size, Image.Resampling.NEAREST)
    radius = max(1, round(DRIFT_EXCLUDE_BANDS * band * small_size[0] / primary.width))
    excluded = small_edited.filter(ImageFilter.MaxFilter(2 * radius + 1))
    kept = ImageChops.invert(excluded)
    kept_fraction = ImageStat.Stat(kept).mean[0] / 255
    if kept_fraction >= DRIFT_MIN_KEPT_FRACTION:
        small_primary = primary.convert("RGB").resize(small_size, Image.Resampling.BILINEAR)
        small_result = result.convert("RGB").resize(small_size, Image.Resampling.BILINEAR)
        drift = max(ImageStat.Stat(ImageChops.difference(small_primary, small_result), kept).mean)
        if drift > DRIFT_THRESHOLD:
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
        # Paste-back already decodes and composites a full frame. A moderate
        # deflate level keeps 2K PNG output within the latency budget while
        # preserving the exact same pixels and alpha.
        save_options["compress_level"] = 5
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
