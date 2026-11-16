"""Leaf value types and payload serializers for staged image edit sources."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class EditImageSource:
    temp_path: Path
    byte_size: int
    filename: str
    content_type: str
    role: Literal["image", "mask", "markup", "reference"] = "image"
    coverage: float | None = None
    # Populated when the source was admitted with Pillow validation (see
    # `copy_edit_source_stream_to_temp(..., validate=True)`); 0 for sources
    # admitted with validation skipped (masks) or rebuilt from a payload saved
    # before this field existed. Callers must treat 0 as "unknown" and fall
    # back to decoding the file when they need the size.
    width: int = 0
    height: int = 0
    raw_sha256: str | None = None
    gallery_image_id: str | None = None


def edit_source_to_payload(source: EditImageSource) -> dict:
    return {
        "temp_path": str(source.temp_path),
        "byte_size": source.byte_size,
        "filename": source.filename,
        "content_type": source.content_type,
        "role": source.role,
        "coverage": source.coverage,
        "width": source.width,
        "height": source.height,
        "raw_sha256": source.raw_sha256,
        "gallery_image_id": source.gallery_image_id,
    }


def edit_source_from_payload(payload: dict) -> EditImageSource:
    role = str(payload.get("role") or "image")
    coverage = payload.get("coverage")
    return EditImageSource(
        temp_path=Path(str(payload.get("temp_path") or "")),
        byte_size=int(payload.get("byte_size") or 0),
        filename=str(payload.get("filename") or "image.png"),
        content_type=str(payload.get("content_type") or "application/octet-stream"),
        role=role if role in {"mask", "markup"} else "image",
        coverage=float(coverage) if coverage is not None else None,
        # Missing on payloads persisted before this field existed; 0 means
        # "decode to find out" to every reader (see EditImageSource docstring).
        width=int(payload.get("width") or 0),
        height=int(payload.get("height") or 0),
        raw_sha256=payload.get("raw_sha256"),
        gallery_image_id=payload.get("gallery_image_id"),
    )
