import io
import time
import pytest
from PIL import Image
from backend.app.core.generated_media import (
    validate_generated_image_bytes, get_generated_image_dimensions,
)

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><rect width="40" height="20" fill="red"/></svg>'


def test_svg_dimensions_and_original():
    assert validate_generated_image_bytes(SVG, filename='a.svg') == 'svg'
    assert get_generated_image_dimensions(SVG) == (40, 20)


@pytest.mark.parametrize('content', [
    '<script/>', '<image href="https://example.com/a"/>',
    '<rect onclick="alert(1)"/>', '<style>@import "https://example.com/a";</style>',
    '<rect fill="url(https://example.com/a)"/>', '<foreignObject/>', '<animate/>',
])
def test_svg_rejects_active_content(content):
    data = f'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">{content}</svg>'.encode()
    with pytest.raises(ValueError):
        validate_generated_image_bytes(data)


def test_gif_preserves_animation_bytes():
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(output, 'GIF', save_all=True,
        append_images=[Image.new('RGB', (8, 8), 'blue')], duration=200, loop=0)
    data = output.getvalue()
    assert validate_generated_image_bytes(data) == 'gif'
    assert get_generated_image_dimensions(data) == (8, 8)
    assert output.getvalue() == data


def test_svg_atomic_save_thumbnail_and_download(client):
    from backend.app.repositories.image_files import save_image_to_temp_with_metadata, promote_image_temp, scan_image_files
    from backend.app.repositories.thumbnails import create_thumbnail_temp_from_path
    path, kind, width, height = save_image_to_temp_with_metadata(SVG, 'safe-original.svg')
    assert (kind, width, height) == ('svg', 40, 20)
    original = promote_image_temp('safe-original.svg', path)
    assert original.read_bytes() == SVG
    assert 'safe-original.svg' in scan_image_files()
    thumbnail = create_thumbnail_temp_from_path(original, original.name)
    if thumbnail is None:
        import subprocess
        import sys
        probe = subprocess.run([sys.executable, '-c', 'import cairosvg.surface'], capture_output=True)
        if probe.returncode != 0:
            pytest.skip('libcairo is unavailable for SVG thumbnail rendering')
    assert thumbnail is not None
    _, thumb = thumbnail
    with Image.open(thumb) as image:
        assert image.size == (40, 20)
    thumb.unlink()
    from backend.app.repositories.gallery.mutations import add_to_gallery_sync
    add_to_gallery_sync(image_id='safe-svg', prompt='vector', size='40x20',
        filename=original.name, metadata={'output_format': 'svg'}, image_bytes=SVG)
    for url in ('/api/image/safe-original.svg', '/api/download/safe-original.svg'):
        response = client.get(url)
        assert response.status_code == 200
        assert response.content == SVG
        assert response.headers['content-type'].startswith('image/svg+xml')
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert 'sandbox' in response.headers['content-security-policy']


def test_gif_truncated_later_frame_rejected():
    output = io.BytesIO()
    Image.new('RGB', (16, 16), 'red').save(output, 'GIF', save_all=True,
        append_images=[Image.new('RGB', (16, 16), 'blue')], duration=200)
    with pytest.raises(ValueError):
        validate_generated_image_bytes(output.getvalue()[:-12])


def test_archive_svg_roundtrip_preserves_metadata(tmp_path):
    import json
    import zipfile
    from backend.app.services.gallery_archive_import import iter_import_gallery_entries
    metadata = {'id': 'vector', 'filename': 'original.svg', 'complexity': 'high',
        'frame_count': None, 'frame_duration_ms': None, 'markup_applied': False}
    archive = tmp_path / 'gallery.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('original.svg', SVG)
        zf.writestr('metadata.json', json.dumps({'images': [metadata]}))
    imported = list(iter_import_gallery_entries(archive))
    assert len(imported) == 1
    data, entry = imported[0]
    assert data == SVG
    for field in ('complexity', 'frame_count', 'frame_duration_ms', 'markup_applied'):
        assert entry[field] == metadata[field]


@pytest.mark.parametrize('width,height', [('1e100', '1e-100'), ('100000001', '0.1')])
def test_svg_bounds_rounded_dimensions(width, height):
    data = f'<svg width="{width}" height="{height}"/>'.encode()
    with pytest.raises(ValueError):
        validate_generated_image_bytes(data)


def test_product_export_import_svg_and_gif(client):
    from backend.app.repositories.gallery.mutations import add_to_gallery_sync
    from backend.tests.support.contract import _wait_for_gallery_import_job
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(output, 'GIF', save_all=True,
        append_images=[Image.new('RGB', (8, 8), 'blue')], duration=200, loop=0)
    originals = {'vector.svg': SVG, 'animation.gif': output.getvalue()}
    for filename, data in originals.items():
        gif = filename.endswith('.gif')
        add_to_gallery_sync(image_id=filename, prompt='roundtrip', size='auto', filename=filename,
            metadata={'output_format': 'gif' if gif else 'svg', 'complexity': 'medium',
                'frame_count': 2 if gif else None, 'frame_duration_ms': 200 if gif else None,
                'markup_applied': False}, image_bytes=data)
    detail = client.get('/api/gallery/vector.svg').json()
    assert (detail['image_width'], detail['image_height']) == (40, 20)
    created = client.post('/api/gallery/direct-export-jobs')
    assert created.status_code == 202
    archive = client.get(created.json()['download_url'])
    assert archive.status_code == 200
    imported = client.post('/api/import?async_job=true',
        files={'archive': ('roundtrip.zip', archive.content, 'application/zip')})
    assert imported.status_code == 202
    finished = _wait_for_gallery_import_job(client, imported.json()['job_id'])
    assert finished['status'] == 'success'
    assert finished['imported_count'] == 2
    entries = client.get('/api/gallery').json()['images']
    restored = [entry for entry in entries if entry['id'] not in originals]
    assert len(restored) == 2
    for entry in restored:
        gif = entry['output_format'] == 'gif'
        assert client.get(entry['image_url']).content == originals['animation.gif' if gif else 'vector.svg']
        assert entry['complexity'] == 'medium'
        assert entry['frame_count'] == (2 if gif else None)
        assert entry['frame_duration_ms'] == (200 if gif else None)


def test_startup_retains_markup_original_and_removes_orphan(client):
    from pathlib import Path
    from backend.app.core import settings as config
    from backend.app.repositories.db import _connect
    from backend.app.repositories.image_files import list_persisted_mask_job_ids
    from backend.app.services.startup_maintenance import cleanup_orphan_mask_files
    with _connect() as conn:
        conn.execute("INSERT INTO generate_jobs (job_id,status,created_at,updated_at,markup_applied,mask_applied) VALUES ('markup-keep','queued','now','now',1,0)")
        conn.commit()
    masks = Path(config.MASKS_DIR)
    masks.mkdir(parents=True, exist_ok=True)
    original = masks / 'markup-keep-markup.png'
    original.write_bytes(b'original')
    orphan = masks / 'orphan-markup.png'
    orphan.write_bytes(b'orphan')
    assert 'markup-keep-markup' in list_persisted_mask_job_ids()
    cleanup_orphan_mask_files()
    assert original.read_bytes() == b'original'
    assert not orphan.exists()


@pytest.mark.parametrize('token', ['url(', 'url(#'])
def test_svg_unterminated_url_is_linear_and_rejected(token):
    payload = token * 40_000
    data = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><style>{payload}</style></svg>'
    ).encode()
    started = time.perf_counter()
    with pytest.raises(ValueError):
        validate_generated_image_bytes(data)
    assert time.perf_counter() - started < 0.2


@pytest.mark.parametrize('fill', ['url(#grad)', "url('#grad')", 'url("#grad")', "url( '#grad' )"])
def test_svg_local_fragment_url_variants_accepted(fill):
    data = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
        '<defs><linearGradient id="grad"/></defs>'
        f'<style>rect{{fill:{fill};}}</style>'
        '<rect width="40" height="20"/>'
        '</svg>'
    ).encode()
    assert validate_generated_image_bytes(data, filename='local.svg') == 'svg'


def test_svg_validation_caches_repeated_parses(monkeypatch):
    from backend.app.core import media, svg_validation
    from backend.app.core.generated_media import (
        detect_generated_image_format,
        validate_generated_image_header_bytes,
    )

    unique_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 43 23">'
        '<rect width="43" height="23" fill="green"/></svg>'
    ).encode()
    calls = 0
    real_fromstring = svg_validation.ElementTree.fromstring

    def spy_fromstring(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_fromstring(*args, **kwargs)

    monkeypatch.setattr(svg_validation.ElementTree, 'fromstring', spy_fromstring)

    assert detect_generated_image_format(unique_svg) == 'svg'
    assert validate_generated_image_header_bytes(unique_svg, filename='cached.svg') == 'svg'
    assert get_generated_image_dimensions(unique_svg) == (43, 23)
    assert media.image_dimension_metadata(unique_svg) == {'image_width': 43, 'image_height': 23}
    assert calls == 1


def test_svg_transient_parse_failure_is_not_cached(monkeypatch):
    from backend.app.core import svg_validation

    data = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 47 29">'
        '<rect width="47" height="29"/></svg>'
    ).encode()
    real_fromstring = svg_validation.ElementTree.fromstring
    state = {'fail': True}

    def flaky_fromstring(*args, **kwargs):
        if state['fail']:
            state['fail'] = False
            raise MemoryError
        return real_fromstring(*args, **kwargs)

    monkeypatch.setattr(svg_validation.ElementTree, 'fromstring', flaky_fromstring)

    with pytest.raises(ValueError):
        svg_validation.validate_svg_bytes(data)
    assert svg_validation.validate_svg_bytes(data) == (47, 29)


def test_validate_image_size_animated_keeps_pixel_ceiling():
    from backend.app.schemas.generation import validate_image_size

    assert validate_image_size('64x64', animated=True) == '64x64'
    with pytest.raises(ValueError, match='8294400'):
        validate_image_size('3840x3840', animated=True)


def test_render_svg_thumbnail_surfaces_stderr_detail(monkeypatch):
    import subprocess
    from backend.app.core import svg_thumbnail

    def fail_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['python'], stderr=b'Traceback...\nOSError: no library called "cairo-2" was found\n')

    monkeypatch.setattr(svg_thumbnail.subprocess, 'run', fail_run)
    with pytest.raises(RuntimeError, match='cairo-2'):
        svg_thumbnail.render_svg_thumbnail(SVG, 128)


def test_validate_edit_markup_alpha_fast_path_and_copy_mask_file(tmp_path, client):
    from backend.app.repositories.image_files import copy_mask_file
    from backend.app.services.edit_markup import validate_edit_markup

    transparent_path = tmp_path / 'markup.png'
    opaque_path = tmp_path / 'opaque.png'
    rgba = Image.new('RGBA', (8, 8), (255, 0, 0, 0))
    rgba.save(transparent_path, 'PNG')
    opaque = Image.new('RGBA', (8, 8), (255, 0, 0, 255))
    opaque.save(opaque_path, 'PNG')

    validate_edit_markup(transparent_path, 8, 8)
    with pytest.raises(ValueError, match='transparent'):
        validate_edit_markup(opaque_path, 8, 8)

    copied = copy_mask_file(transparent_path, 'copy-test-markup.png')
    assert copied.read_bytes() == transparent_path.read_bytes()
