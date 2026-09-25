"""Measure the actual paste_back_image path, including decode and PNG output.

Run: .venv/bin/python backend/tests/support/mask_paste_back_runtime_bench.py
"""

import io
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.app.services.edit_paste_back import paste_back_image


def photo_like(size: int, seed: int) -> Image.Image:
    base = Image.linear_gradient("L").resize((size, size)).convert("RGB")
    noise = Image.effect_noise((size, size), 18).convert("RGB")
    tint = Image.new("RGB", (size, size), (40 + seed, 90, 140))
    return Image.blend(ImageChops.add(base, noise, scale=1.6), tint, 0.3)


def png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        for size, result_size in ((2048, 2048), (4096, 1536)):
            primary = photo_like(size, 0)
            result = photo_like(result_size, 2)
            mask = Image.new("RGBA", (size, size), (0, 0, 0, 255))
            ImageDraw.Draw(mask).ellipse(
                (size * 0.3, size * 0.3, size * 0.7, size * 0.75),
                fill=(0, 0, 0, 0),
            )
            primary_path = Path(directory) / f"primary-{size}.png"
            mask_path = Path(directory) / f"mask-{size}.png"
            primary.save(primary_path)
            mask.save(mask_path)
            result_bytes = png_bytes(result)
            started = time.perf_counter()
            outcome = paste_back_image(result_bytes, primary_path, mask_path)
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"{size}² primary / {result_size}² result: {elapsed_ms:.0f} ms, {outcome.status}, {len(outcome.image_bytes) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
