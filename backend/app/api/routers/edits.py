import asyncio
from dataclasses import replace

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..edit_limits import EDIT_MASK_FIELD_NAME, MAX_EDIT_MASK_BYTES, MAX_EDIT_SOURCE_IMAGES
from ...services.edit_masks import (
    EditMaskInfo,
    validate_edit_mask_against_primary,
    validate_edit_mask_against_primary_path,
)
from ...services.edit_sources import (
    EDIT_SOURCE_SNIFF_BYTES,
    cleanup_edit_sources,
    copy_edit_source_stream_to_temp,
    read_gallery_edit_source,
)
from ...services.job_queue import (
    EditImageSource,
    build_edit_request_from_form,
    queue_edit_job,
)
from ..uploads import is_image_upload, resolve_upload_content_type
from ...core import settings as config
from ...core.media import get_image_dimensions
from ...schemas.generation import EditRequest, GenerateJobResponse
from ...runtime.blocking import run_image_operation


router = APIRouter()


MASK_TOO_LARGE_DETAIL = "Mask is too large. Max size is 4 MB."


def edit_request_from_form(
    prompt: str = Form(...),
    size: str = Form("auto"),
    model: str = Form(""),
    n: int = Form(1),
    quality: str = Form("auto"),
    output_format: str = Form("png"),
    output_compression: int | None = Form(None),
    background: str = Form("auto"),
    response_format: str | None = Form(None),
    webhook_url: str | None = Form(None),
    paste_back: bool | None = Form(None),
    api_preset_id: str | None = Form(None),
) -> EditRequest:
    return build_edit_request_from_form(
        prompt=prompt,
        size=size,
        model=model,
        n=n,
        quality=quality,
        output_format=output_format,
        output_compression=output_compression,
        background=background,
        response_format=response_format,
        webhook_url=webhook_url,
        paste_back=paste_back,
        api_preset_id=api_preset_id,
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


async def validate_edit_mask(mask: EditImageSource, primary: EditImageSource) -> EditMaskInfo:
    try:
        if primary.width > 0 and primary.height > 0:
            return await run_image_operation(
                validate_edit_mask_against_primary,
                mask.temp_path,
                primary_width=primary.width,
                primary_height=primary.height,
                metric_name="validate_edit_mask",
            )
        # Defensive fallback for a primary admitted without a cached size
        # (e.g. a legacy in-flight request); decodes the primary once more.
        return await run_image_operation(
            validate_edit_mask_against_primary_path,
            mask.temp_path,
            primary.temp_path,
            primary_filename=primary.filename,
            primary_content_type=primary.content_type,
            metric_name="validate_edit_mask",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e



@router.post("/api/edits", response_model=GenerateJobResponse, status_code=202)
async def edit_image(
    request: Request,
    req: EditRequest = Depends(edit_request_from_form),
):
    form = await request.form()
    # The mask is streamed to disk before the sources because its size decides
    # whether an EXIF-oriented primary is rotated upright (see
    # `validate_edit_source_file_details`); the header read for that costs
    # nothing and the mask itself is still decoded exactly once.
    mask = await read_upload_edit_mask(form)
    sources: list[EditImageSource] = []
    try:
        sources = await read_upload_edit_sources(
            form,
            orientation_mask_size=mask_dimensions(mask),
        )
        if not sources:
            raise HTTPException(status_code=422, detail="Upload image is required.")
        validate_edit_source_count(sources)
        mask_coverage = None
        mask_optimized_png = None
        if mask is not None:
            mask_info = await validate_edit_mask(mask, sources[0])
            mask_coverage = mask_info.transparent_ratio
            mask_optimized_png = mask_info.optimized_png
        return await queue_edit_job(
            req=req,
            image_sources=sources,
            mask_source=mask,
            mask_coverage=mask_coverage,
            mask_optimized_png=mask_optimized_png,
        )
    except BaseException:
        cleanup_edit_sources(sources if mask is None else [*sources, mask])
        raise


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
    mask = await read_upload_edit_mask(form)
    upload_sources: list[EditImageSource] = []
    try:
        # Uploads here are reference images; the gallery entry is the primary
        # the mask must match, so only it gets the orientation check.
        upload_sources = await read_upload_edit_sources(form)
        gallery_source = await read_gallery_edit_source(
            image_id,
            orientation_mask_size=mask_dimensions(mask),
        )
    except BaseException:
        cleanup_edit_sources([*upload_sources, *([mask] if mask is not None else [])])
        raise
    sources = [gallery_source, *upload_sources]
    try:
        validate_edit_source_count(sources)
        mask_coverage = None
        mask_optimized_png = None
        if mask is not None:
            mask_info = await validate_edit_mask(mask, gallery_source)
            mask_coverage = mask_info.transparent_ratio
            mask_optimized_png = mask_info.optimized_png
        return await queue_edit_job(
            req=req,
            image_sources=sources,
            mask_source=mask,
            mask_coverage=mask_coverage,
            mask_optimized_png=mask_optimized_png,
        )
    except BaseException:
        cleanup_edit_sources(sources if mask is None else [*sources, mask])
        raise
