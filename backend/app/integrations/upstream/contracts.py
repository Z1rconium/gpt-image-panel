"""Shapes exchanged between the upstream modules and their caller.

Callback aliases and the edit-source protocol the HTTP modules agree on, kept
apart from the transport code that uses them.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...schemas.gallery import GalleryEntry


ProgressCallback = Callable[[str, str], None]


class ImageEditSource(Protocol):
    temp_path: Path
    filename: str
    content_type: str
    width: int
    height: int


@dataclass(frozen=True)
class EditUpload:
    """One validated edit file ready for a custom-provider submit."""

    temp_path: Path
    filename: str
    content_type: str
    byte_size: int


@dataclass(frozen=True)
class EditUploads:
    """Reference images and mask for one custom-provider edit task.

    ``inline_variables`` carries the JSON-body form (``{{reference_images}}``
    and ``{{mask}}`` data URLs); ``parts``/``mask_part`` stream the same files
    for multipart bodies. Inline bytes are bounded by
    ``PROVIDER_EDIT_INLINE_MAX_BYTES`` before this object is built.
    """

    parts: tuple[EditUpload, ...]
    mask_part: EditUpload | None
    inline_variables: dict[str, object]


PreviewCallback = Callable[..., None]
PersistGalleryEntry = Callable[..., Awaitable[GalleryEntry]]
