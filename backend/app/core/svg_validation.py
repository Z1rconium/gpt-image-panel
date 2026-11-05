"""Static SVG validation shared by metadata and generated output admission."""
import math
import re
from defusedxml import ElementTree
from . import settings as config


def _svg(data: bytes) -> tuple[int, int]:
    try:
        root = ElementTree.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != '{http://www.w3.org/2000/svg}svg' and root.tag != 'svg':
            raise ValueError('Expected SVG root')
        for node in root.iter():
            tag = node.tag.split('}')[-1].lower()
            if tag in {'script', 'foreignobject', 'image', 'animate', 'animatetransform', 'animatemotion', 'set', 'audio', 'video'}:
                raise ValueError('Active SVG content is forbidden')
            for key, value in node.attrib.items():
                name = key.split('}')[-1].lower()
                if name.startswith('on') or (name in {'href', 'src'} and not value.strip().startswith('#')):
                    raise ValueError('External or active SVG reference')
            text = ' '.join(node.attrib.values()) + (node.text or '')
            if re.search(r'@import|@font-face|\\', text, re.I):
                raise ValueError('Unsafe SVG CSS')
            for url in re.findall(r'url\s*\((.*?)\)', text, re.I | re.S):
                if not url.strip().strip('\"\'').startswith('#'):
                    raise ValueError('External SVG URL')
        view = [float(v) for v in re.split(r'[\s,]+', root.get('viewBox', '').strip()) if v]
        def dimension(name, index):
            value = root.get(name, '')
            match = re.fullmatch(r'([\d.+eE-]+)(?:px)?', value)
            return float(match[1]) if match else (view[index] if len(view) == 4 else 0)
        width, height = dimension('width', 2), dimension('height', 3)
        if not all(math.isfinite(v) and v > 0 for v in (width, height)) or width * height > config.MAX_IMAGE_PIXELS:
            raise ValueError('SVG dimensions exceed image limits')
        width, height = math.ceil(width), math.ceil(height)
        if max(width, height) > min(config.MAX_IMAGE_PIXELS, 2**31 - 1) or width * height > config.MAX_IMAGE_PIXELS:
            raise ValueError('SVG raster dimensions exceed image limits')
        return width, height
    except Exception as exc:
        raise ValueError('Invalid or unsafe SVG image') from exc

