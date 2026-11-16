"""Transparent PNG markup admission, separate from mask semantics."""
from pathlib import Path
from PIL import Image


def validate_edit_markup(path: Path, width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width * height > 16777216:
        raise ValueError('Markup primary must contain at most 16777216 pixels')
    with Image.open(path) as image:
        if image.format != 'PNG':
            raise ValueError('Markup must be PNG')
        if image.size != (width, height):
            raise ValueError('Markup dimensions must match the oriented primary image')
        if "A" in image.getbands():
            alpha_min = image.getchannel("A").getextrema()[0]
        else:
            image.load()
            alpha_min = image.convert("RGBA").getchannel("A").getextrema()[0]
        if alpha_min == 255:
            raise ValueError('Markup must contain transparent pixels')
