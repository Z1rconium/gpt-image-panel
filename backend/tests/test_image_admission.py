"""Unit tests for pure image job admission rules and boundaries."""

from pathlib import Path

import pytest

from backend.app.core.errors import UnprocessableRequestError
from backend.app.core.media import EDIT_INPUT_LIMIT_BYTES, is_below_edit_input_limit
from backend.app.schemas.generation import EditRequest, GenerateRequest
from backend.app.services.edit_masks import EditMaskInfo
from backend.app.services.edit_source_types import EditImageSource
from backend.app.services.image_admission import (
    EditInputs,
    admit_image_job,
    resolve_effective_preset,
)
from backend.app.services.job_queue import resolve_effective_model


def _source(
    *,
    role: str = "image",
    content_type: str = "image/png",
    byte_size: int = 1024,
) -> EditImageSource:
    return EditImageSource(
        temp_path=Path(f"/tmp/{role}.png"),
        byte_size=byte_size,
        filename=f"{role}.png",
        content_type=content_type,
        role=role,  # type: ignore[arg-type]
        width=16,
        height=16,
    )


def _openai_preset(**overrides) -> dict:
    return {
        "id": "default",
        "name": "Default",
        "api_url": "https://api.example.com",
        "api_path": "/v1/images/generations",
        "default_model": "gpt-image-2",
        "default_response_format": "url",
        "provider_kind": "openai",
        "provider_config": None,
        "prompt_guard": False,
        "supports_mask": True,
        **overrides,
    }


def _async_preset(provider_config: dict, **overrides) -> dict:
    return _openai_preset(
        provider_kind="async_json",
        provider_config=provider_config,
        **overrides,
    )


def _async_mapping(**overrides) -> dict:
    mapping = {
        "version": 2,
        "submit": {
            "path": "/jobs",
            "method": "POST",
            "body_format": "json",
            "body": {"prompt": "{{prompt}}"},
        },
        "edit_submit": {
            "path": "/edits",
            "method": "POST",
            "body_format": "multipart",
            "body": {"prompt": "{{prompt}}"},
            "files": {"images": "image", "mask": "mask"},
        },
        "poll": {
            "task_id_path": "$.id",
            "url_template": "/jobs/{{task_id}}",
            "status_path": "$.status",
            "done": ["COMPLETED"],
            "interval_seconds": 0.5,
        },
        "result": {"images_path": "$.images[*].url", "image_kind": "url"},
        "capabilities": {
            "formats": ["png", "jpeg", "webp"],
            "transparent_background": True,
        },
    }
    mapping.update(overrides)
    return mapping


def test_edit_input_limit_boundary():
    assert EDIT_INPUT_LIMIT_BYTES == 50 * 1024 * 1024
    assert is_below_edit_input_limit(EDIT_INPUT_LIMIT_BYTES - 1) is True
    assert is_below_edit_input_limit(EDIT_INPUT_LIMIT_BYTES) is False
    assert is_below_edit_input_limit(EDIT_INPUT_LIMIT_BYTES + 1) is False


def test_resolve_effective_preset_and_model_delegation():
    active = _openai_preset(id="p1", default_model="gpt-image-2")
    alt = _openai_preset(id="p2", default_model="gpt-image-2.6-flare")

    assert resolve_effective_preset(None, active, [active, alt])["id"] == "p1"
    assert resolve_effective_preset("p2", active, [active, alt])["id"] == "p2"
    assert resolve_effective_preset(
        GenerateRequest(prompt="x", api_preset_id="p2"), active, [active, alt]
    )["id"] == "p2"

    with pytest.raises(UnprocessableRequestError, match="API preset not found"):
        resolve_effective_preset("missing", active, [active, alt])

    assert (
        resolve_effective_model(
            "",
            api_preset_id="p2",
            get_active=lambda: active,
            get_presets=lambda: [active, alt],
        )
        == "gpt-image-2.6-flare"
    )
    assert (
        resolve_effective_model(
            "gpt-image-2",
            api_preset_id="missing",
            get_active=lambda: active,
            get_presets=lambda: [active, alt],
        )
        == "gpt-image-2"
    )


def test_admit_image_job_returns_normalized_copy_without_mutating_caller_request():
    req = GenerateRequest(prompt="hello")
    preset = _openai_preset(
        default_model="gpt-image-2.5-flare",
        default_response_format="b64_json",
    )

    admitted = admit_image_job(
        req,
        operation="generation",
        preset=preset,
        api_path="/v1/images/generations",
    )

    assert req.model == "gpt-image-2"
    assert "model" not in req.model_fields_set
    assert req.response_format is None
    assert "response_format" not in req.model_fields_set

    assert admitted.model == "gpt-image-2.5-flare"
    assert admitted.request.model == "gpt-image-2.5-flare"
    assert admitted.request.response_format is None


def test_admit_image_job_preserves_explicit_none_response_format_from_form():
    # Edit form dependencies pass response_format=None explicitly, placing it in
    # model_fields_set so the preset default does not overwrite None.
    form_like = EditRequest(prompt="edit", response_format=None)
    omitted = EditRequest(prompt="edit")
    preset = _openai_preset(default_model="gpt-image-2", default_response_format="b64_json")

    admitted_form = admit_image_job(
        form_like,
        operation="edit",
        preset=preset,
        api_path="/v1/images/edits",
        edit=EditInputs(sources=(_source(),)),
    )
    admitted_omitted = admit_image_job(
        omitted,
        operation="edit",
        preset=preset,
        api_path="/v1/images/edits",
        edit=EditInputs(sources=(_source(),)),
    )

    assert admitted_form.request.response_format is None
    assert admitted_omitted.request.response_format == "b64_json"


@pytest.mark.parametrize(
    ("kwargs", "api_path", "preset_overrides", "expected_detail"),
    [
        (
            {"prompt": "x", "quality": "max"},
            "/v1/images/generations",
            {"default_model": "gpt-image-2"},
            "quality 'max' is not supported for model 'gpt-image-2'",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.5-flare"},
            "/v1/responses",
            {},
            "GPT Image 2.5/2.6 requires /v1/images/generations or /v1/images/edits",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.5-flare", "complexity": "low"},
            "/v1/images/generations",
            {},
            "complexity requires GPT Image 2.6",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2", "output_format": "svg"},
            "/v1/images/generations",
            {},
            "SVG/GIF requires GPT Image 2.6",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif", "partial_images": 1},
            "/v1/images/generations",
            {},
            "SVG/GIF does not support partial_images",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2", "stream": True},
            "/v1/chat/completions",
            {},
            "Streaming preview requires /v1/images/generations, /v1/images/edits or /v1/responses",
        ),
    ],
)
def test_admit_image_job_generation_rejections(
    kwargs, api_path, preset_overrides, expected_detail
):
    req = GenerateRequest(**kwargs)
    with pytest.raises(UnprocessableRequestError, match=expected_detail):
        admit_image_job(
            req,
            operation="generation",
            preset=_openai_preset(**preset_overrides),
            api_path=api_path,
        )


@pytest.mark.parametrize(
    ("req_kwargs", "edit", "preset", "expected_detail"),
    [
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare"},
            EditInputs(sources=(_source(),), mask=_source(role="mask"), markup=_source(role="markup")),
            _openai_preset(),
            "Mask and markup cannot be combined",
        ),
        (
            {"prompt": "x"},
            EditInputs(sources=(_source(),), markup=_source(role="markup")),
            _openai_preset(default_model="gpt-image-2"),
            "Markup requires GPT Image 2.6",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "output_format": "gif"},
            EditInputs(sources=(_source(content_type="image/gif"),), mask=_source(role="mask")),
            _openai_preset(),
            "GIF output does not support edit masks",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare"},
            EditInputs(sources=(_source(content_type="image/gif"),)),
            _openai_preset(),
            "GPT Image 2.5/2.6 edit inputs must be PNG, JPEG or WebP",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.5-flare"},
            EditInputs(sources=(_source(byte_size=EDIT_INPUT_LIMIT_BYTES),)),
            _openai_preset(),
            "GPT Image 2.5/2.6 edit inputs must be smaller than 50 MB",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare"},
            EditInputs(sources=(_source(),), markup=_source(role="markup")),
            _async_preset(_async_mapping()),
            "Provider mapping does not send markup",
        ),
        (
            {"prompt": "x", "model": "gpt-image-2.6-flare", "complexity": "low"},
            EditInputs(sources=(_source(),)),
            _async_preset(
                _async_mapping(
                    submit={
                        "path": "/jobs",
                        "method": "POST",
                        "body_format": "json",
                        "body": {"prompt": "{{prompt}}", "complexity": "{{complexity}}"},
                    }
                )
            ),
            "Provider mapping does not send complexity for this operation",
        ),
    ],
)
def test_admit_image_job_edit_rejections(req_kwargs, edit, preset, expected_detail):
    req = EditRequest(**req_kwargs)
    with pytest.raises(UnprocessableRequestError, match=expected_detail):
        admit_image_job(
            req,
            operation="edit",
            preset=preset,
            api_path="/v1/images/edits",
            edit=edit,
        )


def test_admit_image_job_paste_back_policy_and_50_mib_minus_one_acceptance():
    markup_req = EditRequest(prompt="markup", paste_back=True)
    markup_admitted = admit_image_job(
        markup_req,
        operation="edit",
        preset=_openai_preset(default_model="gpt-image-2.6-flare"),
        api_path="/v1/images/edits",
        edit=EditInputs(
            sources=(_source(byte_size=EDIT_INPUT_LIMIT_BYTES - 1),),
            markup=_source(role="markup", byte_size=EDIT_INPUT_LIMIT_BYTES - 1),
        ),
    )
    assert markup_req.paste_back is True
    assert markup_admitted.request.paste_back is False

    mask_req = EditRequest(prompt="mask")
    mask_info = EditMaskInfo(width=16, height=16, transparent_ratio=0.25, optimized_png=b"png")
    mask_admitted = admit_image_job(
        mask_req,
        operation="edit",
        preset=_openai_preset(),
        api_path="/v1/images/edits",
        edit=EditInputs(sources=(_source(),), mask=_source(role="mask")),
        mask_info=mask_info,
        mask_paste_back_default=False,
    )
    assert mask_req.paste_back is None
    assert mask_admitted.request.paste_back is False
    assert mask_admitted.edit_inputs is not None
    assert mask_admitted.edit_inputs.mask is not None
    assert mask_admitted.edit_inputs.mask.coverage == 0.25
