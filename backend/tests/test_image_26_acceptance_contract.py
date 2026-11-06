"""Main-agent acceptance: actual generated binaries through the public API."""
import base64
import hashlib
import io
import json

import pytest
from PIL import Image

from backend.app.integrations.upstream import generation as upstream
from backend.tests.support.contract import (
    ORIGINAL_CALL_IMAGE_GENERATION_API, ORIGINAL_CALL_IMAGE_EDIT_API,
    _FakePool, _FakePostSession, _FakeResponse, _wait_for_job, backend_main,
    gallery_mutations, media,
)

SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024"><circle cx="512" cy="512" r="300" fill="coral"/></svg>'


def animation():
    out = io.BytesIO()
    Image.new('RGB', (32, 32), 'red').save(
        out, 'GIF', save_all=True, append_images=[Image.new('RGB', (32, 32), 'blue')],
        duration=[100, 200], loop=0,
    )
    return out.getvalue()


class WireSession(_FakePostSession):
    def __init__(self, data, usage=None):
        response = {'data': [{'b64_json': base64.b64encode(data).decode()}]}
        if usage is not None:
            response['usage'] = usage
        super().__init__(_FakeResponse(200, headers={'Content-Type': 'application/json'},
            chunks=[json.dumps(response).encode()],
            peer_ip='93.184.216.34'))
        self.requests = []
        self.forms = []

    def post(self, url, **kwargs):
        self.requests.append(kwargs.get('json'))
        form = kwargs.get('data')
        if form is not None:
            fields = {}
            for options, _headers, value in form._fields:
                if hasattr(value, 'read'):
                    position = value.tell()
                    captured = value.read()
                    value.seek(position)
                else:
                    captured = value
                fields[options['name']] = captured
            self.forms.append(fields)
        return super().post(url, **kwargs)


def wire(monkeypatch, data, usage=None):
    session = WireSession(data, usage)
    monkeypatch.setattr(upstream, 'get_pool', lambda: _FakePool(session))
    monkeypatch.setattr(upstream.ssrf, 'validate_upstream_url', lambda *a, **k: None)
    monkeypatch.setattr(upstream.ssrf, 'validate_response_peer_ip', lambda *a, **k: None)
    monkeypatch.setattr(backend_main.proxy, 'call_image_generation_api', ORIGINAL_CALL_IMAGE_GENERATION_API)
    monkeypatch.setattr(backend_main.proxy, 'call_image_edit_api', ORIGINAL_CALL_IMAGE_EDIT_API)
    return session


@pytest.mark.parametrize('model', ['gpt-image-2.6-flare', 'gpt-image-2.6-sunburst'])
@pytest.mark.parametrize('output_format', ['svg', 'gif'])
def test_special_output_reaches_gallery_and_original_download(client, monkeypatch, model, output_format):
    original = SVG_BYTES if output_format == 'svg' else animation()
    session = wire(monkeypatch, original)
    payload = {'prompt': 'a simple original asset', 'model': model, 'complexity': 'medium',
               'quality': 'max', 'output_format': output_format}
    if output_format == 'gif':
        payload.update(frame_count=2, frame_duration_ms=100)
    accepted = client.post('/api/generate', json=payload)
    assert accepted.status_code == 202, accepted.text
    job = _wait_for_job(client, accepted.json()['job_id'])
    assert job['status'] == 'success', job
    assert job['complexity'] == 'medium'
    assert job['output_format'] == output_format
    assert len(session.requests) == 1
    sent = session.requests[0]
    assert sent['model'] == model and sent['complexity'] == 'medium'
    assert sent['output_format'] == output_format
    for omitted in ('stream', 'partial_images', 'output_compression', 'response_format', 'prompt_rewriter', 'input_fidelity'):
        assert omitted not in sent
    if output_format == 'gif':
        assert sent['frame_count'] == job['frame_count'] == 2
        assert sent['frame_duration_ms'] == job['frame_duration_ms'] == 100
    else:
        assert 'frame_count' not in sent and 'frame_duration_ms' not in sent
    entry = client.get(f"/api/gallery/{job['image_id']}").json()
    assert entry['filename'].endswith('.' + output_format)
    assert entry['output_format'] == output_format and entry['complexity'] == 'medium'
    assert entry['image_width'] == (1024 if output_format == 'svg' else 32)
    assert entry['image_height'] == entry['image_width']
    downloaded = client.get('/api/download/' + entry['filename'])
    assert downloaded.status_code == 200
    assert downloaded.content == original
    assert downloaded.headers['content-type'].startswith('image/svg+xml' if output_format == 'svg' else 'image/gif')
    if output_format == 'gif':
        with Image.open(io.BytesIO(downloaded.content)) as image:
            assert image.n_frames == 2
            image.seek(1)
            assert image.info['duration'] == 200


def png(mode, color):
    out = io.BytesIO()
    Image.new(mode, (64, 64), color).save(out, 'PNG')
    return out.getvalue()


def _gif_edit_request(client, primary, *, from_gallery=False, extra_files=None, **overrides):
    data = {'prompt': 'animate the reference', 'model': 'gpt-image-2.6-flare', 'output_format': 'gif',
            'frame_count': '2', 'frame_duration_ms': '100', **overrides}
    data = {key: value for key, value in data.items() if value is not None}
    files = dict(extra_files or {})
    endpoint = '/api/edits'
    if from_gallery:
        gallery_mutations.add_to_gallery_sync(
            image_id='gif-primary', prompt='primary', size='1024x1024',
            filename='gif-primary.png', image_bytes=primary,
            metadata={'model': 'gpt-image-2', 'output_format': 'png'},
        )
        endpoint += '/from-gallery/gif-primary'
    else:
        files['image'] = ('primary.png', primary, 'image/png')
    return client.post(endpoint, data=data, files=files or None)


@pytest.mark.parametrize('from_gallery', [False, True])
def test_gif_edit_sends_animation_params_and_reaches_gallery(client, monkeypatch, from_gallery):
    original = animation()
    session = wire(monkeypatch, original)
    accepted = _gif_edit_request(client, png('RGB', 'white'), from_gallery=from_gallery)
    assert accepted.status_code == 202, accepted.text
    job = _wait_for_job(client, accepted.json()['job_id'])
    assert job['status'] == 'success', job
    assert job['operation'] == 'edit'
    assert job['output_format'] == 'gif' and job['mask_applied'] is False
    assert job['frame_count'] == 2 and job['frame_duration_ms'] == 100
    assert len(session.forms) == 1
    sent = session.forms[0]
    assert sent['output_format'] == 'gif'
    assert sent['frame_count'] == '2' and sent['frame_duration_ms'] == '100'
    assert not {'mask', 'stream', 'partial_images', 'output_compression', 'response_format'} & sent.keys()
    entry = client.get(f"/api/gallery/{job['image_id']}").json()
    assert entry['filename'].endswith('.gif') and entry['output_format'] == 'gif'
    assert entry['frame_count'] == 2 and entry['frame_duration_ms'] == 100
    downloaded = client.get('/api/download/' + entry['filename'])
    assert downloaded.status_code == 200 and downloaded.content == original


def test_gif_edit_defaults_frames_when_omitted(client, monkeypatch):
    session = wire(monkeypatch, animation())
    accepted = client.post('/api/edits', data={
        'prompt': 'animate the reference', 'model': 'gpt-image-2.6-flare', 'output_format': 'gif',
    }, files={'image': ('primary.png', png('RGB', 'white'), 'image/png')})
    assert accepted.status_code == 202, accepted.text
    assert _wait_for_job(client, accepted.json()['job_id'])['status'] == 'success'
    assert session.forms[0]['frame_count'] == '9' and session.forms[0]['frame_duration_ms'] == '200'


def test_gif_edit_with_mask_is_rejected_before_upstream(client, monkeypatch):
    session = wire(monkeypatch, animation())
    response = _gif_edit_request(client, png('RGB', 'white'), extra_files={
        'mask': ('mask.png', png('RGBA', (0, 0, 0, 0)), 'image/png'),
    })
    assert response.status_code == 422
    assert 'GIF' in response.text and 'mask' in response.text
    assert session.forms == []


@pytest.mark.parametrize('overrides, expected', [
    ({'frame_count': '1'}, 'frame_count'),
    ({'frame_duration_ms': '25'}, 'multiple of 10'),
    ({'output_format': 'png'}, 'GIF-only'),
])
def test_gif_edit_validates_frame_fields(client, monkeypatch, overrides, expected):
    session = wire(monkeypatch, animation())
    response = _gif_edit_request(client, png('RGB', 'white'), **overrides)
    assert response.status_code == 422
    assert expected in response.text
    assert session.forms == []


def test_gif_edit_requires_image_26(client, monkeypatch):
    session = wire(monkeypatch, animation())
    response = _gif_edit_request(client, png('RGB', 'white'), model='gpt-image-2')
    assert response.status_code == 422
    assert 'GPT Image 2.6' in response.text
    assert session.forms == []


def test_svg_edit_is_still_generation_only(client, monkeypatch):
    session = wire(monkeypatch, SVG_BYTES)
    response = _gif_edit_request(client, png('RGB', 'white'), output_format='svg', frame_count=None,
                                 frame_duration_ms=None)
    assert response.status_code == 422
    assert 'SVG' in response.text
    assert session.forms == []


@pytest.mark.parametrize('from_gallery', [False, True])
def test_uploaded_markup_wire_history_restore_and_delete(client, monkeypatch, from_gallery):
    primary = png('RGB', 'white')
    markup = png('RGBA', (255, 0, 0, 100))
    output = png('RGB', 'blue')
    session = wire(monkeypatch, output)
    files = {'markup': ('annotation.png', markup, 'image/png')}
    endpoint = '/api/edits'
    if from_gallery:
        gallery_mutations.add_to_gallery_sync(
            image_id='markup-primary', prompt='primary', size='1024x1024',
            filename='markup-primary.png', image_bytes=primary,
            metadata={'model': 'gpt-image-2', 'output_format': 'png'},
        )
        endpoint += '/from-gallery/markup-primary'
    else:
        files['image'] = ('primary.png', primary, 'image/png')
    accepted = client.post(endpoint, data={
        'prompt': 'follow the red annotation', 'model': 'gpt-image-2.6-flare',
        'quality': 'max', 'complexity': 'low', 'paste_back': 'true',
    }, files=files)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()['job_id']
    job = _wait_for_job(client, job_id)
    assert job['status'] == 'success', job
    assert job['markup_applied'] is True and job['mask_applied'] is False
    assert job['paste_back'] is not True
    assert job['complexity'] == 'low'
    assert len(session.forms) == 1
    sent = session.forms[0]
    assert sent['markup'] == markup and sent['image'] == primary
    assert sent['complexity'] == 'low' and sent['quality'] == 'max'
    assert not {'mask', 'response_format', 'input_fidelity', 'prompt_rewriter'} & sent.keys()
    entry = client.get(f"/api/gallery/{job['image_id']}").json()
    assert entry['markup_applied'] is True
    assert entry['complexity'] == 'low'
    assert client.get('/api/download/' + entry['filename']).content == output
    markup_path = media.safe_mask_path(f'{job_id}-markup.png')
    assert markup_path.is_file()
    assert client.get(f'/api/generate/{job_id}/markup').content == markup
    restore_url = f'/api/generate/{job_id}/markup/restore'
    identity = ({'gallery_image_id': 'markup-primary'} if from_gallery else
                {'source_sha256': hashlib.sha256(primary).hexdigest()})
    restored = client.post(restore_url, data=identity)
    assert restored.status_code == 200, restored.text
    assert restored.content == markup
    assert restored.headers['cache-control'] == 'private, no-store'
    assert client.post(restore_url, data={'source_sha256': '0' * 64}).status_code == 409
    assert client.delete('/api/generate/jobs/history').status_code == 200
    assert not markup_path.exists()


@pytest.mark.parametrize('explicit', [False, True])
def test_svg_job_reports_authoring_cost_gap_and_preserves_usage(client, monkeypatch, explicit):
    usage = {'input_tokens': 10, 'output_tokens': 123}
    if explicit:
        usage['output_tokens_details'] = {'image_tokens': 50}
    wire(monkeypatch, SVG_BYTES, usage)
    accepted = client.post('/api/generate', json={
        'prompt': 'a vector', 'model': 'gpt-image-2.6-flare', 'output_format': 'svg',
    })
    assert accepted.status_code == 202
    job = _wait_for_job(client, accepted.json()['job_id'])
    assert job['status'] == 'success', job
    assert job['usage']['image_output_tokens'] == (50 if explicit else None)
    assert job['cost']['complete'] is False
    assert job['cost']['estimated_cost_usd'] is None
    assert 'author' in job['cost']['reason'].lower()
