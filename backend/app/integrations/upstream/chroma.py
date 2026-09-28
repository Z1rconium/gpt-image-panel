"""Remove a requested solid-color backdrop from generated images."""

from collections import deque
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageChops, ImageFilter

from ...core.media import detect_image_format, verified_pillow_image


@dataclass(frozen=True)
class ChromaOutcome:
    image_bytes: bytes
    status: str


def remove_chroma_background(image_bytes: bytes, mode: str) -> ChromaOutcome:
    image_format = detect_image_format(image_bytes)
    if image_format is None:
        raise ValueError("Generated image format is not supported")
    with verified_pillow_image(lambda: Image.open(BytesIO(image_bytes)), expected_format=image_format) as source:
        image = source.convert("RGBA")
    width, height = image.size
    pixels = image.load()
    if pixels is None:
        raise ValueError("Could not decode generated image pixels")

    def matches(x: int, y: int, strict: bool = False) -> bool:
        r, g, b, _ = pixels[x, y]
        if mode == "chroma_green":
            return (g >= (190 if strict else 135) and r <= (85 if strict else 140)
                    and b <= (85 if strict else 140) and g - max(r, b) >= (100 if strict else 60))
        return (min(r, b) >= (190 if strict else 135) and g <= (85 if strict else 140)
                and min(r, b) - g >= (100 if strict else 60))

    edge = set()
    for x in range(width):
        edge.add((x, 0))
        edge.add((x, height - 1))
    for y in range(height):
        edge.add((0, y))
        edge.add((width - 1, y))
    seeds = [point for point in edge if matches(*point, strict=True)]
    if len(seeds) < max(1, int(len(edge) * 0.05)):
        return ChromaOutcome(image_bytes, "not_detected")

    visited = bytearray(width * height)
    queue = deque(seeds)
    for x, y in seeds:
        visited[y * width + x] = 1
    removed = 0
    while queue:
        x, y = queue.popleft()
        removed += 1
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue
            index = ny * width + nx
            if not visited[index] and matches(nx, ny):
                visited[index] = 1
                queue.append((nx, ny))
    if removed < max(1, int(width * height * 0.02)):
        return ChromaOutcome(image_bytes, "not_detected")

    # Blur only the cutout boundary. Connected-component selection above keeps
    # matching colors enclosed by the subject opaque.
    alpha = Image.frombytes("L", (width, height), bytes(255 if not value else 0 for value in visited))
    alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.8))
    image.putalpha(ImageChops.multiply(image.getchannel("A"), alpha))
    output = BytesIO()
    image.save(output, format="PNG")
    return ChromaOutcome(output.getvalue(), "applied")
