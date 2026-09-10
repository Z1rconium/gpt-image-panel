"""Images API capabilities verified against the OpenAI reference on 2026-09-10."""

from typing import Literal

MAX_PROMPT_CHARS = 32000
ImageQuality = Literal["auto", "low", "medium", "high", "xhigh", "max"]
BASE_IMAGE_QUALITIES = ("auto", "low", "medium", "high")
IMAGE_25_MODELS = frozenset({
    "gpt-image-2.5-flare",
    "gpt-image-2.5-flare-2026-09-08",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-sunburst-2026-09-08",
})


def is_image_25(model: str | None) -> bool:
    return str(model or "").strip() in IMAGE_25_MODELS


def image_qualities(model: str | None) -> tuple[str, ...]:
    return BASE_IMAGE_QUALITIES + (("xhigh", "max") if is_image_25(model) else ())


def validate_image_model_options(model: str, quality: str, api_path: str) -> None:
    if quality not in image_qualities(model):
        raise ValueError(f"quality '{quality}' is not supported for model '{model}'")
    if is_image_25(model) and api_path not in {"/v1/images/generations", "/v1/images/edits"}:
        raise ValueError("GPT Image 2.5 requires /v1/images/generations or /v1/images/edits")
