"""Shapes exchanged between the upstream modules and their caller.

Callback aliases and the edit-source protocol the HTTP modules agree on, kept
apart from the transport code that uses them.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from ...schemas.gallery import GalleryEntry


ProgressCallback = Callable[[str, str], None]


class ImageEditSource(Protocol):
    temp_path: Path
    filename: str
    content_type: str


PreviewCallback = Callable[[int, str, bytes], None]
PersistGalleryEntry = Callable[..., Awaitable[GalleryEntry]]
