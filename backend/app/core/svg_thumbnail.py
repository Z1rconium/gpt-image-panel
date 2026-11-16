"""Isolated, thumbnail-sized SVG renderer. Never fetch external resources."""
import subprocess
import sys
from .svg_validation import validate_svg_bytes


def render_svg_thumbnail(data: bytes, max_side: int) -> bytes:
    width, height = validate_svg_bytes(data)
    scale = min(1, max_side / max(width, height))
    script = '''
import sys
from cairosvg.surface import PNGSurface
def deny(*args, **kwargs):
    raise ValueError("External SVG resource forbidden")
PNGSurface.convert(bytestring=sys.stdin.buffer.read(), unsafe=False,
    url_fetcher=deny, output_width=int(sys.argv[1]), output_height=int(sys.argv[2]),
    write_to=sys.stdout.buffer)
'''
    result = subprocess.run(
        [sys.executable, '-c', script, str(max(1, round(width * scale))), str(max(1, round(height * scale)))],
        input=data, capture_output=True, timeout=10, check=True,
    )
    return result.stdout
