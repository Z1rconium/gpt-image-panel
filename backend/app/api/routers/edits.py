"""Image edit upload, gallery source staging, and queue admission routes."""

import asyncio
from dataclasses import replace

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..edit_limits import EDIT_MASK_FIELD_NAME, MAX_EDIT_MASK_BYTES, MAX_EDIT_SOURCE_IMAGES
from ..uploads import is_image_upload, resolve_upload_content_type
from ...core import overall_config
from ...core import settings as config
from ...core.media import EDIT_INPUT_LIMIT_BYTES, get_image_dimensions
from ...runtime.blocking import run_image_operation
from ...schemas.generation import EditRequest, GenerateJobResponse
from ...services.edit_source_types import EditImageSource
from ...services.edit_sources import (
    EDIT_SOURCE_SNIFF_BYTES,
    cleanup_edit_sources,
    copy_edit_source_stream_to_temp,
    read_gallery_edit_source,
    read_gallery_reference_edit_sources,
)
from ...services.image_admission import EditInputs, admit_edit
from ...services.job_queue import build_edit_request_from_form, enqueue


router = APIRouter()


MASK_TOO_LARGE_DETAIL = "Mask is too large. Max size is 4 MB."


def parse_optional_form_bool(name: str, value: str | None) -> bool | None:
    """Parse an optional boolean form field without relying on framework coercion.

    FastAPI 0.157 turns a missing ``bool | None = Form(None)`` into ``False``,
    which would silently disable a server-side default. Reading the raw string
    keeps "absent" distinct from an explicit false on every supported version.
    """
    if value is None:
        return None
    try:
        return overall_config.parse_bool(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name} must be a boolean") from exc


def edit_request_from_form(
    prompt: str = Form(...),
    size: str = Form("auto"),
    model: str = Form(""),
    n: int = Form(1),
    quality: str = Form("auto"),
    complexity: str | None = Form(None),
    output_format: str = Form("png"),
    frame_count: int | None = Form(None),
    frame_duration_ms: int | None = Form(None),
    output_compression: int | None = Form(None),
    background: str = Form("auto"),
    response_format: str | None = Form(None),
    webhook_url: str | None = Form(None),
    paste_back: str | None = Form(None),
    api_preset_id: str | None = Form(None),
    safety_identifier: str | None = Form(None),
) -> EditRequest:
    return build_edit_request_from_form(
        prompt=prompt,
        size=size,
        model=model,
        n=n,
        quality=quality,
        complexity=complexity,
        output_format=output_format,
        frame_count=frame_count,
        frame_duration_ms=frame_duration_ms,
        output_compression=output_compression,
        background=background,
        response_format=response_format,
        webhook_url=webhook_url,
        paste_back=parse_optional_form_bool("paste_back", paste_back),
        api_preset_id=api_preset_id,
        safety_identifier=safety_identifier,
    )



def mask_dimensions(mask: EditImageSource | None) -> tuple[int, int] | None:
    """Mask pixel size straight from its header, without decoding it.

    The primary's orientation normalization needs to know whether the mask was
    drawn against the raw or the rotated pixels, and that decision has to be
    made before the primary is decoded — `validate_edit_mask_file()` still does
    the only full mask decode, later.
    """
    if mask is None:
        return None
    try:
        with mask.temp_path.open("rb") as file:
            header = file.read(EDIT_SOURCE_SNIFF_BYTES)
    except OSError:
        return None
    return get_image_dimensions(header)





def extract_reference_image_ids(form: FormData) -> list[str]:
    ids: list[str] = []
    for field in ("reference_image_id", "reference_image_id[]"):
        for value in form.getlist(field):
            if isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    ids.append(trimmed)
    return list(dict.fromkeys(ids))


def pre_validate_edit_source_count(
    *,
    has_gallery_primary: bool,
    uploads_count: int,
    reference_ids_count: int,
):
    total = (1 if has_gallery_primary else 0) + uploads_count + reference_ids_count
    if total > MAX_EDIT_SOURCE_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_EDIT_SOURCE_IMAGES} edit source images are supported.",
        )
    if total == 0:
        raise HTTPException(status_code=422, detail="Upload image is required.")


def validate_edit_source_count(sources: list[EditImageSource]):
    if len(sources) > MAX_EDIT_SOURCE_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_EDIT_SOURCE_IMAGES} edit source images are supported.",
        )


async def read_upload_edit_source(
    image: UploadFile,
    orientation_mask_size: tuple[int, int] | None = None,
) -> EditImageSource:
    if not is_image_upload(image):
        raise HTTPException(status_code=400, detail="Upload must be an image file.")

    image_content_type = resolve_upload_content_type(image)
    filename = image.filename or "image.png"
    return await run_image_operation(
        copy_edit_source_stream_to_temp,
        image.file,
        filename,
        image_content_type,
        empty_detail="Uploaded image is empty.",
        too_large_detail=(
            f"Uploaded image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB."
        ),
        read_error_detail="Failed to read uploaded image",
        metric_name="copy_validate_edit_upload",
        orientation_mask_size=orientation_mask_size,
    )


async def read_upload_edit_sources(
    form: FormData,
    *,
    orientation_mask_size: tuple[int, int] | None = None,
) -> list[EditImageSource]:
    uploads: list[UploadFile] = []
    for field_name in ("image", "image[]"):
        for value in form.getlist(field_name):
            if isinstance(value, StarletteUploadFile):
                uploads.append(value)

    if len(uploads) > MAX_EDIT_SOURCE_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_EDIT_SOURCE_IMAGES} edit source images are supported.",
        )

    sources: list[EditImageSource] = []
    # Only the first upload is the primary image the mask has to match, so only
    # that one gets the orientation compatibility check.
    results = await asyncio.gather(
        *(
            read_upload_edit_source(upload, orientation_mask_size if index == 0 else None)
            for index, upload in enumerate(uploads)
        ),
        return_exceptions=True,
    )
    sources = [result for result in results if isinstance(result, EditImageSource)]
    error = next(
        (result for result in results if isinstance(result, BaseException)),
        None,
    )
    if error is not None:
        cleanup_edit_sources(sources)
        raise error
    return sources


async def read_upload_edit_mask(form: FormData) -> EditImageSource | None:
    values = form.getlist(EDIT_MASK_FIELD_NAME)
    if not values:
        return None
    if len(values) > 1:
        raise HTTPException(status_code=400, detail="Only one mask is supported.")

    upload = values[0]
    if not isinstance(upload, StarletteUploadFile):
        raise HTTPException(status_code=400, detail="Mask must be a PNG file.")
    if not is_image_upload(upload):
        raise HTTPException(status_code=400, detail="Mask must be a PNG file.")
    if resolve_upload_content_type(upload) != "image/png":
        raise HTTPException(status_code=400, detail="Mask must be a PNG file.")

    filename = upload.filename or "mask.png"
    source = await run_image_operation(
        copy_edit_source_stream_to_temp,
        upload.file,
        filename,
        "image/png",
        empty_detail="Mask file is empty.",
        too_large_detail=MASK_TOO_LARGE_DETAIL,
        read_error_detail="Failed to read mask upload",
        metric_name="copy_validate_edit_mask_upload",
        max_bytes=MAX_EDIT_MASK_BYTES,
        # validate_edit_mask() decodes the mask itself; skip the redundant
        # Pillow pass here so a masked edit decodes the mask exactly once.
        validate=False,
    )
    return replace(source, role="mask")


async def read_upload_edit_markup(form: FormData) -> EditImageSource | None:
    values = form.getlist("markup")
    if not values:
        return None
    if form.getlist("mask"):
        raise HTTPException(status_code=422, detail="Mask and markup cannot be combined")
    if len(values) != 1 or not isinstance(values[0], StarletteUploadFile):
        raise HTTPException(status_code=422, detail="Upload one PNG markup layer")
    upload = values[0]
    source = await run_image_operation(
        copy_edit_source_stream_to_temp, upload.file, upload.filename or "markup.png", "image/png",
        max_bytes=min(config.MAX_FILE_SIZE_MB * 1024 * 1024, EDIT_INPUT_LIMIT_BYTES - 1), validate=True,
        empty_detail="Markup is empty", too_large_detail="Markup must be smaller than 50 MiB",
        read_error_detail="Failed to read markup", metric_name="copy_edit_markup",
    )
    return replace(source, role="markup")



async def _stage_and_queue_edit(
    req: EditRequest,
    form: FormData,
    *,
    gallery_primary_id: str | None = None,
) -> GenerateJobResponse:
    markup: EditImageSource | None = None
    mask: EditImageSource | None = None
    sources: list[EditImageSource] = []
    try:
        # The mask is streamed to disk before the sources because its size decides
        # whether an EXIF-oriented primary is rotated upright (see
        # `validate_edit_source_file_details`); the header read for that costs
        # nothing and the mask itself is still decoded exactly once.
        markup = await read_upload_edit_markup(form)
        mask = await read_upload_edit_mask(form)
        ref_ids = extract_reference_image_ids(form)
        if gallery_primary_id is not None:
            ref_ids = [ref_id for ref_id in ref_ids if ref_id != gallery_primary_id]
        raw_uploads = [
            v
            for k in ("image", "image[]")
            for v in form.getlist(k)
            if isinstance(v, StarletteUploadFile)
        ]
        has_gallery_primary = gallery_primary_id is not None
        pre_validate_edit_source_count(
            has_gallery_primary=has_gallery_primary,
            uploads_count=len(raw_uploads),
            reference_ids_count=len(ref_ids),
        )

        mask_dim = mask_dimensions(mask)
        if has_gallery_primary:
            # Uploads here are reference images; the gallery entry is the primary
            # the mask must match, so only it gets the orientation check.
            gallery_source = await read_gallery_edit_source(
                gallery_primary_id,
                orientation_mask_size=mask_dim,
            )
            sources.append(gallery_source)
            if raw_uploads:
                sources.extend(await read_upload_edit_sources(form))
            if ref_ids:
                sources.extend(await read_gallery_reference_edit_sources(ref_ids))
        else:
            if raw_uploads:
                sources.extend(
                    await read_upload_edit_sources(
                        form,
                        orientation_mask_size=mask_dim,
                    )
                )
                if ref_ids:
                    sources.extend(await read_gallery_reference_edit_sources(ref_ids))
            else:
                sources.extend(
                    await read_gallery_reference_edit_sources(
                        ref_ids,
                        orientation_mask_size=mask_dim,
                    )
                )

        if not sources:
            raise HTTPException(status_code=422, detail="Upload image is required.")

        validate_edit_source_count(sources)
        admitted = await admit_edit(
            req,
            EditInputs(
                sources=tuple(sources),
                mask=mask,
                markup=markup,
            ),
        )
        return await enqueue(admitted, queued_message="Queued image edit")
    except BaseException:
        cleanup_edit_sources([*sources, *([mask] if mask else []), *([markup] if markup else [])])
        raise


@router.post("/api/edits", response_model=GenerateJobResponse, status_code=202)
async def edit_image(
    request: Request,
    req: EditRequest = Depends(edit_request_from_form),
):
    form = await request.form()
    return await _stage_and_queue_edit(req, form)


@router.post(
    "/api/edits/from-gallery/{image_id}",
    response_model=GenerateJobResponse,
    status_code=202,
)
async def edit_image_from_gallery(
    request: Request,
    image_id: str,
    req: EditRequest = Depends(edit_request_from_form),
):
    form = await request.form()
    return await _stage_and_queue_edit(req, form, gallery_primary_id=image_id)
