import asyncio
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..edit_limits import EDIT_MASK_FIELD_NAME, MAX_EDIT_MASK_BYTES, MAX_EDIT_SOURCE_IMAGES
from ...services.edit_masks import (
    EditMaskInfo,
    validate_edit_mask_against_primary,
    validate_edit_mask_against_primary_path,
)
from ...services.gallery_archive_shared import max_upload_bytes
from ...services.job_queue import (
    EditImageSource,
    build_edit_request_from_form,
    queue_edit_job,
)
from ..uploads import is_image_upload, resolve_upload_content_type
from ...services.uploads import validate_upload_image_bytes
from ...core import settings as config
from ...repositories.gallery.queries import get_gallery_entry
from ...core.media import get_image_dimensions, image_content_type_for_filename, safe_image_path
from ...repositories.image_files import validate_and_normalize_image_file
from ...schemas.generation import EditRequest, GenerateJobResponse
from ...runtime.blocking import run_db_operation, run_image_operation


router = APIRouter()


EDIT_SOURCE_SNIFF_BYTES = 512
EDIT_SOURCE_CHUNK_BYTES = 1024 * 1024
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
    )


def create_edit_source_temp_path(filename: str) -> tuple[int, Path]:
    suffix = Path(filename or "").suffix.lower() or ".img"
    temp_dir = Path(config.DATA_DIR) / "edit-sources"
    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="edit-source-",
        suffix=suffix,
        dir=temp_dir,
    )
    return fd, Path(temp_name)


def validate_edit_source_header(
    image_header: bytes,
    byte_size: int,
    filename: str,
    content_type: str,
    *,
    empty_detail: str,
    too_large_detail: str,
    max_bytes: int | None = None,
):
    if byte_size == 0:
        raise HTTPException(status_code=400, detail=empty_detail)
    limit = max_upload_bytes() if max_bytes is None else max_bytes
    if byte_size > limit:
        raise HTTPException(status_code=400, detail=too_large_detail)
    validate_upload_image_bytes(image_header, filename, content_type)


def validate_edit_source_file_details(
    path: Path,
    filename: str,
    content_type: str,
    *,
    orientation_mask_size: tuple[int, int] | None = None,
) -> tuple[int, int, int]:
    """Full Pillow decode, returning the size and byte size so callers don't decode again.

    A photo that carries an EXIF orientation is rotated upright and re-encoded
    in the same decode (see `validate_and_normalize_image_file`), because the
    browser shows and the mask editor draws the rotated pixels while Pillow
    reports the raw ones — a mismatch that rejects non-square phone photos with
    a 422 and silently edits the wrong region on square ones.

    Used for primary/reference images; mask uploads skip this (see
    `copy_edit_source_stream_to_temp(..., validate=False)`) because
    `validate_edit_mask_file()` decodes them once on its own.
    """
    try:
        _format, width, height, byte_size = validate_and_normalize_image_file(
            path,
            filename=filename,
            content_type=content_type,
            orientation_mask_size=orientation_mask_size,
        )
        return width, height, byte_size
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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


def copy_edit_source_file_to_temp(
    path: Path,
    filename: str,
    content_type: str,
    *,
    empty_detail: str,
    too_large_detail: str,
    read_error_detail: str,
    max_bytes: int | None = None,
    orientation_mask_size: tuple[int, int] | None = None,
) -> EditImageSource:
    try:
        with path.open("rb") as source:
            return copy_edit_source_stream_to_temp(
                source,
                filename,
                content_type,
                empty_detail=empty_detail,
                too_large_detail=too_large_detail,
                read_error_detail=read_error_detail,
                max_bytes=max_bytes,
                orientation_mask_size=orientation_mask_size,
            )
    except HTTPException:
        raise
    except OSError as e:
        raise HTTPException(status_code=500, detail=read_error_detail) from e


def copy_edit_source_stream_to_temp(
    source,
    filename: str,
    content_type: str,
    *,
    empty_detail: str,
    too_large_detail: str,
    read_error_detail: str,
    max_bytes: int | None = None,
    validate: bool = True,
    orientation_mask_size: tuple[int, int] | None = None,
) -> EditImageSource:
    """Copy an upload to a temp edit-source file.

    `validate=False` skips the full Pillow decode (still does the cheap magic-
    byte header sniff) for uploads that will be fully decoded once, later, by
    a caller-specific validator — currently only mask uploads, whose
    alpha/dimension checks in `validate_edit_mask_file()` already decode the
    file. Width/height stay 0 when the decode is skipped.

    `byte_size` is the size of the file as it ends up on disk, so an EXIF
    orientation re-encode (which changes the byte count) still reserves the
    right amount of pending-upload memory.
    """
    limit = max_upload_bytes() if max_bytes is None else max_bytes
    fd, temp_path = create_edit_source_temp_path(filename)
    total = 0
    header = bytearray()

    try:
        source.seek(0)
        with os.fdopen(fd, "wb") as target:
            while True:
                chunk = source.read(EDIT_SOURCE_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(status_code=400, detail=too_large_detail)
                if len(header) < EDIT_SOURCE_SNIFF_BYTES:
                    header.extend(chunk[: EDIT_SOURCE_SNIFF_BYTES - len(header)])
                target.write(chunk)
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except OSError as e:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=read_error_detail) from e
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    width = height = 0
    try:
        validate_edit_source_header(
            bytes(header),
            total,
            filename,
            content_type,
            empty_detail=empty_detail,
            too_large_detail=too_large_detail,
            max_bytes=max_bytes,
        )
        if validate:
            width, height, total = validate_edit_source_file_details(
                temp_path,
                filename,
                content_type,
                orientation_mask_size=orientation_mask_size,
            )
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return EditImageSource(temp_path, total, filename, content_type, width=width, height=height)


def cleanup_edit_sources(sources: list[EditImageSource]):
    for source in sources:
        source.temp_path.unlink(missing_ok=True)


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


async def read_gallery_edit_source(
    image_id: str,
    *,
    orientation_mask_size: tuple[int, int] | None = None,
) -> EditImageSource:
    entry = await run_db_operation(
        get_gallery_entry,
        image_id,
        metric_name="get_gallery_edit_source",
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    path = safe_image_path(entry.filename)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Gallery image file not found")

    image_content_type = image_content_type_for_filename(path.name)

    return await run_image_operation(
        copy_edit_source_file_to_temp,
        path,
        path.name,
        image_content_type,
        empty_detail="Gallery image is empty",
        too_large_detail=f"Gallery image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB.",
        read_error_detail="Failed to read gallery image",
        metric_name="copy_validate_gallery_edit_source",
        orientation_mask_size=orientation_mask_size,
    )


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
