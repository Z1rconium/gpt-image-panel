import pytest
from pydantic import ValidationError
from backend.app.schemas.generation import GenerateRequest, EditRequest
from backend.app.integrations.upstream.payloads import _build_image_params


@pytest.mark.parametrize('model', ['gpt-image-2.6-flare', 'gpt-image-2.6-sunburst'])
def test_image_26_gif_wire(model):
    request = GenerateRequest(prompt='animate', model=model, output_format='gif', size='32x32', complexity='low', quality='max')
    data = _build_image_params(request)
    assert data['frame_count'] == 9
    assert data['frame_duration_ms'] == 200
    assert data['complexity'] == 'low'
    assert not {'response_format', 'input_fidelity', 'output_compression', 'partial_images', 'stream'} & data.keys()


@pytest.mark.parametrize('options', [
    {'output_format': 'svg', 'n': 2},
    {'output_format': 'gif', 'stream': True},
    {'output_format': 'gif', 'output_compression': 90},
    {'output_format': 'gif', 'frame_duration_ms': 25},
    {'output_format': 'png', 'frame_count': 9},
    {'output_format': 'svg', 'background': 'chroma_green'},
])
def test_image_26_rejects_invalid_combinations(options):
    with pytest.raises(ValidationError):
        GenerateRequest(prompt='x', model='gpt-image-2.6-flare', **options)


def test_image_26_formats_are_generation_only():
    with pytest.raises(ValidationError):
        EditRequest(prompt='x', model='gpt-image-2.6-flare', output_format='svg')


def test_old_models_keep_constraints():
    with pytest.raises(ValidationError):
        GenerateRequest(prompt='x', size='32x32')
    request = GenerateRequest(prompt='x', complexity='low')
    with pytest.raises(ValueError):
        _build_image_params(request)


def test_provider_26_capabilities_are_explicit():
    from copy import deepcopy
    from backend.tests.test_provider_mapping_v2_contract import V2_CONFIG
    from backend.app.schemas.provider import resolve_provider_config
    config = deepcopy(V2_CONFIG)
    old = resolve_provider_config(config)
    assert old.capabilities.formats == ('jpeg', 'png', 'webp')
    assert not old.capabilities.complexity
    config['submit']['body'].update(complexity='{{complexity}}', count='{{frame_count}}', duration='{{frame_duration_ms}}')
    config['capabilities'] = {'formats': ['svg', 'gif']}
    resolved = resolve_provider_config(config)
    assert resolved.capabilities.complexity
    assert resolved.capabilities.animation
    assert set(resolved.capabilities.formats) == {'svg', 'gif'}


@pytest.mark.asyncio
@pytest.mark.parametrize('body_format', ['json', 'multipart'])
async def test_provider_markup_upload_packaging(tmp_path, body_format):
    from types import SimpleNamespace
    from backend.app.integrations.upstream.generation import _build_provider_edit_uploads
    path = tmp_path / 'markup.png'
    path.write_bytes(b'original-markup')
    source = SimpleNamespace(temp_path=path, filename='markup.png', content_type='image/png', byte_size=15)
    uploads = await _build_provider_edit_uploads(SimpleNamespace(body_format=body_format), [], None, source)
    if body_format == 'json':
        import base64
        assert uploads.inline_variables['markup'] == 'data:image/png;base64,' + base64.b64encode(b'original-markup').decode()
    else:
        assert uploads.markup_part.temp_path.read_bytes() == b'original-markup'


def test_markup_rejects_opaque_and_wrong_dimensions(tmp_path):
    from PIL import Image
    from backend.app.services.edit_markup import validate_edit_markup
    path = tmp_path / 'markup.png'
    Image.new('RGBA', (32, 16), (255, 0, 0, 255)).save(path)
    with pytest.raises(ValueError, match='transparent'):
        validate_edit_markup(path, 32, 16)
    with pytest.raises(ValueError, match='dimensions'):
        validate_edit_markup(path, 16, 32)


def test_markup_primary_uses_upright_exif_dimensions(tmp_path):
    from PIL import Image
    from backend.app.services.edit_sources import validate_edit_source_file_details
    path = tmp_path / 'primary.jpg'
    image = Image.new('RGB', (32, 16))
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    width, height, _ = validate_edit_source_file_details(path, 'primary.jpg', 'image/jpeg')
    assert (width, height) == (16, 32)


def test_malformed_png_is_domain_error(tmp_path):
    from backend.app.services.edit_sources import validate_edit_source_file_details
    from backend.app.core.errors import InvalidRequestError
    path = tmp_path / 'broken.png'
    path.write_bytes(b'\x89PNG\r\n\x1a\ntruncated')
    with pytest.raises(InvalidRequestError):
        validate_edit_source_file_details(path, 'broken.png', 'image/png')


def test_svg_usage_preserves_raw_without_inventing_image_tokens():
    from backend.app.core.image_cost import normalize_usage, estimate_image_cost
    raw = {'output_tokens': 123, 'input_tokens': 10}
    svg = normalize_usage(raw, output_format='svg')
    assert svg['raw'] == raw
    assert svg['image_output_tokens'] is None
    cost = estimate_image_cost('gpt-image-2.6-flare', svg, output_format='svg')
    assert not cost['complete']
    assert cost['estimated_cost_usd'] is None
    assert 'author' in cost['reason']
    assert normalize_usage(raw)['image_output_tokens'] == 123


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_markup_copy_removed_on_queue_rejection_or_cancel(tmp_path, monkeypatch, cancel):
    import asyncio
    from backend.app.services import job_queue
    source_path = tmp_path / 'source.png'
    source_path.write_bytes(b'original')
    persisted = {}
    monkeypatch.setattr(job_queue, 'write_mask_file', lambda data, name: persisted.update({name: data}))
    monkeypatch.setattr(job_queue, 'delete_mask_file', lambda name: persisted.pop(name, None))
    async def reject(**kwargs):
        assert any(s['role'] == 'markup' for s in kwargs['edit_sources_payload'])
        assert persisted
        if cancel:
            raise asyncio.CancelledError()
        raise RuntimeError('queue full')
    monkeypatch.setattr(job_queue, 'queue_image_job', reject)
    source = job_queue.EditImageSource(temp_path=source_path, filename='source.png', byte_size=8, content_type='image/png')
    markup = job_queue.replace(source, role='markup')
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await job_queue.queue_edit_job(EditRequest(prompt='x'), [source], markup_source=markup)
    assert persisted == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('body_format', ['json', 'multipart'])
async def test_provider_markup_actual_submit_wire(tmp_path, body_format):
    from types import SimpleNamespace
    from backend.app.integrations.upstream import async_provider
    from backend.app.integrations.upstream.generation import _build_provider_edit_uploads
    from backend.tests.test_custom_provider_protocol_contract import _ScriptedSession, _json
    path = tmp_path / 'markup.png'
    path.write_bytes(b'original-layer')
    source = SimpleNamespace(temp_path=path, filename='markup.png', content_type='image/png', byte_size=14)
    submit = SimpleNamespace(body_format=body_format, method='POST', files_images='images', files_mask='', files_markup='annotation')
    uploads = await _build_provider_edit_uploads(submit, [], None, source)
    body = {'complexity': 'low', 'frames': 9, 'duration': 200, **uploads.inline_variables}
    session = _ScriptedSession({('POST', 'https://mapped.example.com/edit'): [_json({'ok': True})]})
    await async_provider._submit_provider_request(session, submit, 'https://mapped.example.com/edit', headers={}, socks5_proxy='socks5://proxy', body=body, uploads=uploads)
    sent = session.calls[0][2]
    if body_format == 'json':
        assert sent['json']['markup'].startswith('data:image/png;base64,')
        assert sent['json']['complexity'] == 'low'
    else:
        fields = sent['data']._fields
        by_name = {field[0]['name']: field for field in fields}
        assert by_name['annotation'][0]['filename'] == 'markup.png'
        assert by_name['complexity'][2] == 'low'
        assert by_name['frames'][2] == '9'


@pytest.mark.asyncio
async def test_markup_uses_saved_preset_default_model(tmp_path, monkeypatch):
    from backend.app.api.routers.edits import validate_uploaded_markup
    from backend.app.services import job_queue
    from PIL import Image
    path = tmp_path / 'markup.png'
    Image.new('RGBA', (16, 16), (0, 0, 0, 0)).save(path)
    source = job_queue.EditImageSource(temp_path=path, byte_size=path.stat().st_size, filename='markup.png', content_type='image/png', width=16, height=16, role='markup')
    monkeypatch.setattr('backend.app.api.routers.edits.get_active_preset', lambda: {'default_model': 'gpt-image-2.6-flare'})
    req = EditRequest(prompt='x', model='')
    await validate_uploaded_markup(source, source, req)
    assert req.paste_back is False


def test_edit_complexity_requires_edit_template_not_generation_template(client, monkeypatch):
    from copy import deepcopy
    from backend.tests.test_custom_provider_protocol_contract import EDIT_MAPPING, _make_mapped_preset
    from backend.tests.support.contract import PNG_BYTES
    mapping = deepcopy(EDIT_MAPPING)
    mapping['submit']['body']['complexity'] = '{{complexity}}'
    _make_mapped_preset(client, monkeypatch, mapping)
    response = client.post('/api/edits', data={'prompt': 'x', 'model': 'gpt-image-2.6-flare', 'complexity': 'low'}, files={'image': ('x.png', PNG_BYTES, 'image/png')})
    assert response.status_code == 422
    assert 'for this operation' in response.text


@pytest.mark.parametrize('model', ['gpt-image-2.6-flare', 'gpt-image-2.6-sunburst'])
def test_edit_inherits_preset_model_with_markup_over_http(client, monkeypatch, model):
    from backend.tests.test_image_26_acceptance_contract import png, wire
    from backend.tests.support.contract import _settings_payload, _wait_for_job
    settings = client.get('/api/settings').json()
    saved = client.post('/api/settings', json=_settings_payload(settings, default_model=model))
    assert saved.status_code == 200, saved.text
    session = wire(monkeypatch, png('RGB', 'blue'))
    response = client.post('/api/edits', data={'prompt': 'follow markup'}, files={
        'image': ('primary.png', png('RGB', 'white'), 'image/png'),
        'markup': ('markup.png', png('RGBA', (255, 0, 0, 100)), 'image/png'),
    })
    assert response.status_code == 202, response.text
    job = _wait_for_job(client, response.json()['job_id'])
    assert job['status'] == 'success', job
    assert job['model'] == model
    assert session.forms[0]['model'] == model


@pytest.mark.parametrize('model', ['gpt-image-2.6-flare', 'gpt-image-2.6-sunburst'])
def test_image_26_rejects_gif_edit_source(client, model):
    import io
    from PIL import Image
    data = io.BytesIO()
    Image.new('RGB', (32, 32)).save(data, 'GIF')
    response = client.post('/api/edits', data={'prompt': 'x', 'model': model}, files={'image': ('x.gif', data.getvalue(), 'image/gif')})
    assert response.status_code == 422
    assert 'PNG, JPEG or WebP' in response.text


@pytest.mark.parametrize('explicit', [False, True])
def test_svg_preserves_explicit_image_usage_only(explicit):
    from backend.app.core.image_cost import normalize_usage, estimate_image_cost
    raw = {'input_tokens': 10, 'output_tokens': 123}
    if explicit:
        raw['output_tokens_details'] = {'image_tokens': 87}
    usage = normalize_usage(raw, output_format='svg')
    assert usage['image_output_tokens'] == (87 if explicit else None)
    assert usage['raw'] == raw
    cost = estimate_image_cost('gpt-image-2.6-sunburst', usage, output_format='svg')
    assert cost['complete'] is False
    assert cost['estimated_cost_usd'] is None
    assert 'author' in cost['reason']


@pytest.mark.parametrize('output_format', ['svg', 'gif'])
def test_special_format_rejects_explicit_partial_images_http(client, output_format):
    response = client.post('/api/generate', json={
        'prompt': 'x', 'model': 'gpt-image-2.6-flare',
        'output_format': output_format, 'partial_images': 2,
    })
    assert response.status_code == 422
    assert 'partial_images' in response.text


@pytest.mark.parametrize('output_format', ['svg', 'gif'])
def test_special_format_recovery_keeps_implicit_partial_default(output_format):
    from backend.app.services.job_queue import build_request_payload, rebuild_request
    original = GenerateRequest(prompt='x', model='gpt-image-2.6-flare', output_format=output_format)
    assert 'partial_images' not in original.model_fields_set
    snapshot = build_request_payload(original)
    assert snapshot['partial_images'] == 2
    recovered = rebuild_request('generation', snapshot)
    assert recovered.output_format == output_format
    assert recovered.partial_images == 2
    data = _build_image_params(recovered)
    assert 'partial_images' not in data
