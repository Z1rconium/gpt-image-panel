import aiohttp
import asyncio
import base64
import json
import logging
import time
from contextlib import asynccontextmanager
from collections.abc import (
    Callable,
    Sequence,
)
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core import settings as config
from ...core.api_paths import (
    CHAT_COMPLETIONS_API_PATH,
    RESPONSES_API_PATH,
    build_upstream_url,
    normalize_api_path,
)
from ...core.constants import PROVIDER_EDIT_INLINE_MAX_BYTES
from ...core.observability import observe_job_stage, record_upstream_usage
from ...core.diagnostics import UnitDiagnostics
from ...core import validators as ssrf
from ...core.media import (
    detect_image_format,
    generate_image_id,
    get_image_dimensions,
    stable_image_id_for,
    validate_image_header_bytes,
)
from ...core.observability import metrics
from ...runtime.blocking import run_file_operation, run_image_operation, upstream_memory_lease
from ...schemas.gallery import GalleryEntry
from ...schemas.generation import EditRequest, GenerateRequest
from ...schemas.provider import ResolvedEditSubmit, resolve_provider_config
from ..session_pool import (
    TIMEOUT_UPSTREAM,
    get_pool,
)
from .async_provider import (
    CheckpointCallback,
    ShouldCancelRemote,
    run_async_provider,
)
from .contracts import EditUpload, EditUploads, ImageEditSource
from .chroma import remove_chroma_background
from .diagnostics import image_diagnostics
from .edit_paste_back import PasteBackOutcome, paste_back_image

ProgressCallback = Callable[[str, str], None]
# Called with (partial_image_index, mime_type, image_bytes) as each streamed
# partial image arrives. Never called for non-streaming requests.
# Persists one decoded image and returns the stored gallery entry. The caller
# supplies it so this module stays free of the persistence layer.
logger = logging.getLogger(__name__)


def upstream_task_memory_weight(response_format: str | None) -> int:
    image_bytes = config.MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB * 1024 * 1024
    if response_format == "url":
        return image_bytes + 1024 * 1024
    response_bytes = config.MAX_UPSTREAM_JSON_MB * 1024 * 1024
    return response_bytes + image_bytes


from .errors import (
    UpstreamApiError,
    UpstreamImageDownloadError,
    _warn_if_socks5_upstream_resolves_private,
)
from .payloads import (
    DETECTED_FORMAT_EXTENSIONS,
    validate_upstream_image_data,
)
from .transport import DOWNLOAD_CONCURRENCY
from .payloads import (
    _build_image_params,
    build_chat_completions_request_data,
    build_gallery_metadata,
    build_responses_request_data,
    extract_chat_completion_image_results,
    extract_response_image_results,
    extract_usage_from_sse_events,
    get_image_transfer_stage,
    get_output_format_info,
    is_json_content_type,
    looks_like_json_body,
    reported_image_fields,
    sent_generation_prompt,
)
from .transport import (
    extract_image_bytes,
    iter_bounded_sse_json_events,
    parse_upstream_chat_completion_response,
    parse_upstream_json_response,
    read_limited_text_response,
    validate_generated_image_bytes,
)
from .errors import raise_upstream_error
from .contracts import (
    PersistGalleryEntry,
)


@dataclass(frozen=True)
class PreparedUpstreamRequest:
    session: aiohttp.ClientSession
    headers: dict[str, str]
    upstream_url: str
    socks5_proxy: str | None

    @asynccontextmanager
    async def post(self, **kwargs: Any):
        async with self.session.post(
            self.upstream_url,
            headers=self.headers,
            allow_redirects=False,
            **kwargs,
        ) as resp:
            if not self.socks5_proxy:
                ssrf.validate_response_peer_ip(resp, "Upstream API")
            yield resp


async def _prepare_upstream_request(
    *,
    api_url: str,
    api_key: str,
    api_path: str,
    socks5_proxy: str | None,
    json_content_type: bool = True,
) -> PreparedUpstreamRequest:
    upstream_url = build_upstream_url(api_url, api_path)
    await _warn_if_socks5_upstream_resolves_private(upstream_url, socks5_proxy)
    await ssrf.validate_upstream_url_async(upstream_url, config.UPSTREAM_HOST_ALLOWLIST)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "opencode",
    }
    if json_content_type:
        headers["Content-Type"] = "application/json"

    session = get_pool().get(timeout_kind=TIMEOUT_UPSTREAM, socks5_proxy=socks5_proxy)
    return PreparedUpstreamRequest(
        session=session,
        headers=headers,
        upstream_url=upstream_url,
        socks5_proxy=socks5_proxy,
    )

async def save_gallery_entries_from_upstream_data(
    *,
    download_session: aiohttp.ClientSession,
    data: list[dict[str, Any]],
    response_preview: str,
    payload: GenerateRequest,
    format_extension: str,
    gallery_metadata: dict[str, Any],
    save_message: str,
    progress: ProgressCallback | None,
    persist_gallery_entry: PersistGalleryEntry,
    transform_image: Callable[[bytes], Any] | None = None,
    chroma_mode: str | None = None,
    prompt_guard: bool = False,
) -> list[GalleryEntry]:
    data = validate_upstream_image_data(data, payload.n)
    max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024
    max_task_bytes = config.MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB * 1024 * 1024
    decoded_task_bytes = 0
    total = len(data)

    async def process_one(image_index: int, image_data: dict) -> GalleryEntry:
        nonlocal decoded_task_bytes
        transfer_stage, transfer_message = get_image_transfer_stage(image_data)
        if progress:
            progress(
                transfer_stage,
                f"{transfer_message} ({image_index + 1}/{total})",
            )
        with observe_job_stage("download_decode"):
            image_bytes = await extract_image_bytes(
                download_session,
                image_data,
                response_preview,
                max_bytes,
            )
        try:
            if progress:
                progress(
                    "validating_image_bytes",
                    f"Validating decoded image ({image_index + 1}/{total})",
                )
            with observe_job_stage("validate"):
                if len(image_bytes) > max_bytes:
                    raise UpstreamImageDownloadError(
                        f"Image too large: {len(image_bytes)} bytes (max {max_bytes})"
                    )
                decoded_task_bytes += len(image_bytes)
                if decoded_task_bytes > max_task_bytes:
                    raise UpstreamImageDownloadError(
                        "Decoded images exceed per-task byte budget: "
                        f"{decoded_task_bytes} bytes (max {max_task_bytes})"
                    )

                detected_format = detect_image_format(image_bytes)
                detected_extension = DETECTED_FORMAT_EXTENSIONS.get(
                    detected_format or "",
                    format_extension,
                )
                image_id = stable_image_id_for(image_index) or generate_image_id()
                filename = f"{image_id}.{detected_extension}"
                validate_image_header_bytes(image_bytes, filename=filename)
            entry_metadata = {**gallery_metadata}
            entry_metadata.update(reported_image_fields(image_data))
            entry_metadata.update({
                key: image_data[key]
                for key in ("revised_prompt", "reported_size", "reported_quality")
                if isinstance(image_data.get(key), str) and image_data[key]
            })
            entry_metadata["diagnostics"] = image_diagnostics(
                payload,
                str(gallery_metadata.get("api_path") or ""),
                prompt_guard=prompt_guard,
                sent_prompt=str(gallery_metadata.get("sent_prompt") or payload.prompt),
                revised_prompt=entry_metadata.get("revised_prompt"),
                reported_size=entry_metadata.get("reported_size"),
                reported_quality=entry_metadata.get("reported_quality"),
                actual_size=get_image_dimensions(image_bytes),
            )
            if chroma_mode:
                if progress:
                    progress("chroma_removal", f"Removing solid background ({image_index + 1}/{total})")
                with observe_job_stage("chroma_removal"):
                    chroma = await run_image_operation(
                        remove_chroma_background, image_bytes, chroma_mode,
                        metric_name="remove_chroma_background",
                    )
                image_bytes = chroma.image_bytes
                entry_metadata["chroma_status"] = chroma.status
                if chroma.status == "applied":
                    detected_format = "png"
                    detected_extension = "png"
                    filename = f"{image_id}.png"
            if transform_image is not None:
                if progress:
                    progress("paste_back", f"Pasting masked edit ({image_index + 1}/{total})")
                with observe_job_stage("paste_back"):
                    outcome: PasteBackOutcome = await transform_image(image_bytes)
                image_bytes = outcome.image_bytes
                entry_metadata.update(outcome.metadata)
                metric_status = outcome.status.replace(":", ".")
                metrics.increment(f"image_jobs.paste_back.{metric_status}")
                detected_format = detect_image_format(image_bytes)
                detected_extension = DETECTED_FORMAT_EXTENSIONS.get(
                    detected_format or "", format_extension
                )
                filename = f"{image_id}.{detected_extension}"
            if len(image_bytes) > max_bytes:
                raise UpstreamImageDownloadError(
                    f"Processed image too large: {len(image_bytes)} bytes (max {max_bytes})"
                )
            if detected_format:
                entry_metadata["output_format"] = detected_format

            if progress:
                progress(
                    "saving_images",
                    f"{save_message} ({image_index + 1}/{total})",
                )
            entry = await persist_gallery_entry(
                image_bytes=image_bytes,
                image_id=image_id,
                prompt=payload.prompt,
                size=payload.size,
                filename=filename,
                metadata=entry_metadata,
            )
            return entry
        finally:
            del image_bytes

    if total <= 1 or any(item.get("b64_json") for item in data):
        entries = [await process_one(0, data[0])]
        for image_index, image_data in enumerate(data[1:], start=1):
            entries.append(await process_one(image_index, image_data))
    else:
        sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

        async def bounded(idx: int, img: dict) -> GalleryEntry:
            async with sem:
                return await process_one(idx, img)

        entries = list(
            await asyncio.gather(*(bounded(i, d) for i, d in enumerate(data)))
        )
    return entries


async def consume_streaming_image_response(
    resp: aiohttp.ClientResponse,
    api_path: str,
    progress: ProgressCallback | None,
    preview: "PreviewCallback | None",
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Parse a `stream=true` image generation/edit response.

    Forwards each partial image to `preview` as it arrives, using a bounded
    SSE parser so a slow or malicious upstream can't grow memory unbounded.
    Returns the final image (in the same `data` array shape a non-streaming
    response uses) plus usage, taken from the upstream's `*.completed` event.
    """
    status = resp.status
    content_type = resp.headers.get("Content-Type", "")
    if status >= 400:
        max_response_bytes = config.MAX_UPSTREAM_JSON_MB * 1024 * 1024
        error_text = await read_limited_text_response(
            resp, max_response_bytes, label="Upstream stream error"
        )
        is_json_response = is_json_content_type(content_type) or looks_like_json_body(error_text)
        raise_upstream_error(status, error_text, is_json_response, api_path)

    if "text/event-stream" not in content_type:
        raise UpstreamApiError(
            "Upstream did not return a streaming response for stream=true. "
            "Disable streaming preview for this request and try again."
        )

    if progress:
        progress("received_api_response", "Receiving streamed upstream response")

    final_data: list[dict[str, Any]] | None = None
    raw_usage: dict[str, Any] | None = None
    next_partial_index = 0
    max_total_bytes = config.STREAMING_MAX_TOTAL_MB * 1024 * 1024
    max_frame_bytes = config.STREAMING_MAX_FRAME_MB * 1024 * 1024

    async for event in iter_bounded_sse_json_events(
        resp,
        max_total_bytes=max_total_bytes,
        max_frame_bytes=max_frame_bytes,
        label="Upstream image stream",
    ):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type.endswith(".partial_image"):
            # Images API frames carry `b64_json`; Responses API frames carry
            # `partial_image_b64`. Text deltas and other unrelated events are
            # skipped because neither field is present.
            b64_json = event.get("b64_json") or event.get("partial_image_b64")
            if not b64_json:
                continue
            try:
                partial_index = int(event.get("partial_image_index"))
            except (TypeError, ValueError):
                partial_index = next_partial_index
            next_partial_index = partial_index + 1
            if preview is not None:
                try:
                    image_bytes = base64.b64decode(str(b64_json))
                except ValueError:
                    continue
                mime_type = f"image/{str(event.get('output_format') or 'png').lower()}"
                if progress:
                    progress(
                        "streaming_preview",
                        f"Received partial preview {partial_index + 1}",
                    )
                preview(partial_index, mime_type, image_bytes)
        elif event_type in {"response.completed", "response.incomplete"}:
            response_payload = event.get("response")
            if not isinstance(response_payload, dict):
                continue
            responses_data = extract_response_image_results(response_payload)
            if responses_data:
                final_data = [dict(item) for item in responses_data]
            usage = response_payload.get("usage")
            if isinstance(usage, dict) and usage:
                raw_usage = usage
            if event_type == "response.incomplete" and not responses_data:
                details = response_payload.get("incomplete_details")
                reason = (
                    details.get("reason")
                    if isinstance(details, dict) and details.get("reason")
                    else "the upstream ended the response before completion"
                )
                raise UpstreamApiError(f"Streaming response ended incomplete: {reason}")
        elif event_type.endswith(".completed"):
            b64_json = event.get("b64_json")
            if b64_json:
                final_data = [{"b64_json": b64_json, **reported_image_fields(event)}]
            usage = event.get("usage")
            if isinstance(usage, dict):
                raw_usage = usage
        elif event_type.endswith(".failed") or event_type == "error":
            message = event.get("error") or event.get("message")
            if isinstance(message, dict):
                message = message.get("message")
            if not message and isinstance(event.get("response"), dict):
                response_error = event["response"].get("error")
                if isinstance(response_error, dict):
                    message = response_error.get("message") or response_error.get("code")
            raise UpstreamApiError(str(message or "Upstream reported a streaming failure"))

    if final_data is None:
        raise UpstreamApiError(
            "Streaming upstream response ended without a completed image event"
        )

    return final_data, raw_usage


async def _call_async_provider_api(
    api_url: str,
    api_key: str,
    api_path: str,
    payload: GenerateRequest,
    api_preset_name: str | None,
    progress: ProgressCallback | None,
    socks5_proxy: str | None,
    *,
    provider_config: dict,
    persist_gallery_entry: PersistGalleryEntry,
    prompt_guard: bool,
    async_remote: dict[str, Any] | None = None,
    async_checkpoint: CheckpointCallback | None = None,
    async_cancel_remote: ShouldCancelRemote | None = None,
    async_diagnostics: UnitDiagnostics | None = None,
) -> list[GalleryEntry]:
    api_path = normalize_api_path(api_path)
    payload.normalize_model_options(api_path)

    format_info = get_output_format_info(payload.output_format)
    chroma_mode = payload.background if payload.background.startswith("chroma_") else None
    gallery_metadata = build_gallery_metadata(payload, api_path, api_preset_name)
    gallery_metadata["sent_prompt"] = sent_generation_prompt(payload, prompt_guard=prompt_guard)

    download_session = get_pool().get(timeout_kind=TIMEOUT_UPSTREAM)
    memory_lease = upstream_memory_lease(upstream_task_memory_weight(None))
    await memory_lease.__aenter__()
    try:
        upstream_started = time.monotonic()
        with observe_job_stage("upstream_wait"):
            data, response_preview = await run_async_provider(
                api_url=api_url,
                api_key=api_key,
                provider_config=provider_config,
                payload=payload,
                progress=progress,
                socks5_proxy=socks5_proxy,
                prompt_guard=prompt_guard,
                remote=async_remote,
                checkpoint=async_checkpoint,
                should_cancel_remote=async_cancel_remote,
                diagnostics=async_diagnostics,
            )
        gallery_metadata["upstream_duration_ms"] = max(
            0, round((time.monotonic() - upstream_started) * 1000)
        )
        data = validate_upstream_image_data(data, payload.n)
        return await save_gallery_entries_from_upstream_data(
            download_session=download_session,
            data=data,
            response_preview=response_preview,
            payload=payload,
            format_extension=format_info["extension"],
            gallery_metadata=gallery_metadata,
            save_message="Saving generated images",
            progress=progress,
            persist_gallery_entry=persist_gallery_entry,
            chroma_mode=chroma_mode,
            prompt_guard=prompt_guard,
        )
    finally:
        await memory_lease.__aexit__(None, None, None)


def _read_source_bytes(path: Path) -> bytes:
    return path.read_bytes()


async def _build_provider_edit_uploads(
    edit_submit: ResolvedEditSubmit,
    image_sources: Sequence[ImageEditSource],
    mask_source: ImageEditSource | None,
) -> EditUploads:
    """Package validated edit sources for a custom-provider submit.

    JSON edits inline bounded data URLs; multipart edits stream the validated
    temp files directly, so no base64 copy is ever held in memory.
    """
    def part_of(source: ImageEditSource) -> EditUpload:
        return EditUpload(
            temp_path=source.temp_path,
            filename=source.filename or "image.png",
            content_type=source.content_type or "application/octet-stream",
            byte_size=int(getattr(source, "byte_size", 0) or 0),
        )

    parts = tuple(part_of(source) for source in image_sources)
    mask_part: EditUpload | None = None
    inline: dict[str, Any] = {}
    if edit_submit.body_format == "json":
        total_bytes = sum(part.byte_size for part in parts)
        if mask_source is not None:
            total_bytes += int(getattr(mask_source, "byte_size", 0) or 0)
        if total_bytes > PROVIDER_EDIT_INLINE_MAX_BYTES:
            raise UpstreamApiError(
                "Edit sources exceed the inline data-URL budget for this provider "
                f"mapping ({total_bytes} bytes; max {PROVIDER_EDIT_INLINE_MAX_BYTES})"
            )
        encoded: list[str] = []
        for part in parts:
            data = await run_file_operation(
                _read_source_bytes, part.temp_path, metric_name="read_provider_edit_source"
            )
            encoded.append(f"data:{part.content_type};base64,{base64.b64encode(data).decode('ascii')}")
        inline["reference_images"] = encoded
        if mask_source is not None:
            data = await run_file_operation(
                _read_source_bytes, mask_source.temp_path, metric_name="read_provider_edit_mask"
            )
            content_type = mask_source.content_type or "image/png"
            inline["mask"] = f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"
    elif mask_source is not None:
        mask_part = part_of(mask_source)
    return EditUploads(parts=parts, mask_part=mask_part, inline_variables=inline)


async def call_image_provider_edit_api(
    api_url: str,
    api_key: str,
    payload: EditRequest,
    image_sources: Sequence[ImageEditSource],
    api_preset_name: str | None = None,
    progress: ProgressCallback | None = None,
    socks5_proxy: str | None = None,
    *,
    provider_config: dict,
    persist_gallery_entry: PersistGalleryEntry,
    prompt_guard: bool = False,
    mask_source: ImageEditSource | None = None,
    mask_coverage: float | None = None,
    async_remote: dict[str, Any] | None = None,
    async_checkpoint: CheckpointCallback | None = None,
    async_cancel_remote: ShouldCancelRemote | None = None,
    async_diagnostics: UnitDiagnostics | None = None,
) -> list[GalleryEntry]:
    """Run one edit task through a declaratively mapped custom provider."""
    if not image_sources:
        raise UpstreamApiError("At least one edit source image is required")
    api_path = "/v1/images/edits"
    payload.normalize_model_options(api_path)
    resolved = resolve_provider_config(provider_config)
    edit_submit = resolved.edit_submit
    if edit_submit is None:
        raise UpstreamApiError(
            "Provider mapping does not declare image edit support; "
            "add an edit_submit section to provider_config"
        )
    uploads = await _build_provider_edit_uploads(edit_submit, image_sources, mask_source)

    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = build_gallery_metadata(
        payload,
        api_path,
        api_preset_name,
        mask_coverage=mask_coverage,
    )
    gallery_metadata["sent_prompt"] = sent_generation_prompt(payload, prompt_guard=prompt_guard)

    transform_image = None
    if mask_source is not None:
        if payload.paste_back is False:
            gallery_metadata["paste_back"] = "skipped:disabled"
            metrics.increment("image_jobs.paste_back.skipped.disabled")
        else:

            async def transform_image(image_bytes: bytes) -> PasteBackOutcome:
                return await run_image_operation(
                    paste_back_image,
                    image_bytes,
                    image_sources[0].temp_path,
                    mask_source.temp_path,
                    output_format=payload.output_format,
                    output_compression=payload.output_compression,
                    background=payload.background,
                    metric_name="paste_back_image",
                )

    download_session = get_pool().get(timeout_kind=TIMEOUT_UPSTREAM)
    paste_back_weight = (
        max(0, image_sources[0].width or 0) * max(0, image_sources[0].height or 0) * 12
        if transform_image is not None
        else 0
    )
    memory_lease = upstream_memory_lease(upstream_task_memory_weight(None) + paste_back_weight)
    await memory_lease.__aenter__()
    try:
        upstream_started = time.monotonic()
        with observe_job_stage("upstream_wait"):
            data, response_preview = await run_async_provider(
                api_url=api_url,
                api_key=api_key,
                provider_config=provider_config,
                payload=payload,
                progress=progress,
                socks5_proxy=socks5_proxy,
                prompt_guard=prompt_guard,
                remote=async_remote,
                checkpoint=async_checkpoint,
                should_cancel_remote=async_cancel_remote,
                diagnostics=async_diagnostics,
                edit=uploads,
            )
        gallery_metadata["upstream_duration_ms"] = max(
            0, round((time.monotonic() - upstream_started) * 1000)
        )
        data = validate_upstream_image_data(data, payload.n)
        return await save_gallery_entries_from_upstream_data(
            download_session=download_session,
            data=data,
            response_preview=response_preview,
            payload=payload,
            format_extension=format_info["extension"],
            gallery_metadata=gallery_metadata,
            save_message="Saving edited images",
            progress=progress,
            persist_gallery_entry=persist_gallery_entry,
            transform_image=transform_image,
            chroma_mode=None,
            prompt_guard=prompt_guard,
        )
    finally:
        await memory_lease.__aexit__(None, None, None)


async def call_image_generation_api(
    api_url: str,
    api_key: str,
    api_path: str,
    payload: GenerateRequest,
    api_preset_name: str | None = None,
    progress: ProgressCallback | None = None,
    socks5_proxy: str | None = None,
    *,
    stream: bool = False,
    partial_images: int = 2,
    preview: "PreviewCallback | None" = None,
    persist_gallery_entry: PersistGalleryEntry,
    prompt_guard: bool = False,
    provider_config: dict | None = None,
    async_remote: dict[str, Any] | None = None,
    async_checkpoint: CheckpointCallback | None = None,
    async_cancel_remote: ShouldCancelRemote | None = None,
    async_diagnostics: UnitDiagnostics | None = None,
) -> list[GalleryEntry]:
    if provider_config is not None:
        if stream:
            raise UpstreamApiError("Streaming preview is not available for async providers")
        return await _call_async_provider_api(
            api_url,
            api_key,
            api_path,
            payload,
            api_preset_name,
            progress,
            socks5_proxy,
            provider_config=provider_config,
            persist_gallery_entry=persist_gallery_entry,
            prompt_guard=prompt_guard,
            async_remote=async_remote,
            async_checkpoint=async_checkpoint,
            async_cancel_remote=async_cancel_remote,
            async_diagnostics=async_diagnostics,
        )
    api_path = normalize_api_path(api_path)
    payload.normalize_model_options(api_path)
    if payload.background.startswith("chroma_") and api_path != "/v1/images/generations":
        raise UpstreamApiError("Local chroma removal requires /v1/images/generations")
    use_streaming = bool(stream) and api_path in {"/v1/images/generations", RESPONSES_API_PATH}
    prepared_request = await _prepare_upstream_request(
        api_url=api_url,
        api_key=api_key,
        api_path=api_path,
        socks5_proxy=socks5_proxy,
    )

    if api_path == RESPONSES_API_PATH:
        if progress:
            progress("building_responses_payload", "Building Responses API payload")
        request_data = build_responses_request_data(
            payload,
            prompt_guard=prompt_guard,
            stream=use_streaming,
            partial_images=partial_images,
        )
    elif api_path == CHAT_COMPLETIONS_API_PATH:
        if progress:
            progress(
                "building_chat_completions_payload",
                "Building Chat Completions API payload",
            )
        request_data = build_chat_completions_request_data(payload, prompt_guard=prompt_guard)
    else:
        if progress:
            progress("building_generation_payload", "Building image generation payload")
        request_data = _build_image_params(payload, prompt_guard=prompt_guard)
        if use_streaming:
            request_data["stream"] = True
            request_data["partial_images"] = max(1, min(3, int(partial_images or 2)))

    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = build_gallery_metadata(payload, api_path, api_preset_name)
    gallery_metadata["sent_prompt"] = sent_generation_prompt(payload, prompt_guard=prompt_guard)

    pool = get_pool()
    download_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM)

    if progress:
        progress("waiting_for_api", "Waiting for upstream API response")
    response_format = (
        None
        if api_path in {RESPONSES_API_PATH, CHAT_COMPLETIONS_API_PATH}
        else payload.response_format
    )
    memory_lease = upstream_memory_lease(
        upstream_task_memory_weight(response_format)
    )
    await memory_lease.__aenter__()
    try:
        upstream_started = time.monotonic()
        with observe_job_stage("upstream_wait"):
            async with prepared_request.post(
                json=request_data,
            ) as resp:
                if use_streaming:
                    data, raw_usage = await consume_streaming_image_response(
                        resp, api_path, progress, preview
                    )
                    record_upstream_usage(raw_usage)
                    response_text = ""
                elif api_path == CHAT_COMPLETIONS_API_PATH:
                    result, response_text = (
                        await parse_upstream_chat_completion_response(
                            resp, api_path, progress
                        )
                    )
                else:
                    result, response_text = await parse_upstream_json_response(
                        resp, api_path, progress
                    )

        gallery_metadata["upstream_duration_ms"] = max(0, round((time.monotonic() - upstream_started) * 1000))
        if not use_streaming:
            raw_usage = result.get("usage") if isinstance(result, dict) else None
            if raw_usage is None and isinstance(result, dict) and "_sse_events" in result:
                raw_usage = extract_usage_from_sse_events(result.get("_sse_events") or [])
            record_upstream_usage(raw_usage)

            if api_path == RESPONSES_API_PATH:
                if progress:
                    progress(
                        "extracting_response_image_output",
                        "Extracting image_generation_call output",
                    )
                data = extract_response_image_results(result)
            elif api_path == CHAT_COMPLETIONS_API_PATH:
                if progress:
                    progress(
                        "extracting_chat_completion_image_output",
                        "Extracting Chat Completions image output",
                    )
                data = extract_chat_completion_image_results(result)
            else:
                if progress:
                    progress("extracting_generation_data", "Extracting image data array")
                raw_data = result.get("data", [])
                data = [
                    {**item, **reported_image_fields(result)} if isinstance(item, dict) else item
                    for item in raw_data
                ] if isinstance(raw_data, list) else raw_data
        data = validate_upstream_image_data(data, payload.n)
        if not data:
            raise UpstreamApiError(
                f"No image data in upstream response: {response_text[:200]}"
            )

        response_preview = response_text[:200]
        del response_text
        if not use_streaming:
            del result
        return await save_gallery_entries_from_upstream_data(
            download_session=download_session,
            data=data,
            response_preview=response_preview,
            payload=payload,
            format_extension=format_info["extension"],
            gallery_metadata=gallery_metadata,
            save_message="Saving generated images",
            progress=progress,
            persist_gallery_entry=persist_gallery_entry,
            chroma_mode=payload.background if payload.background.startswith("chroma_") else None,
            prompt_guard=prompt_guard,
        )
    finally:
        await memory_lease.__aexit__(None, None, None)


async def call_image_generation_preview_api(
    api_url: str,
    api_key: str,
    payload: GenerateRequest,
    *,
    socks5_proxy: str | None = None,
) -> bytes:
    """Generate and validate one image without creating gallery or job records."""
    api_path = "/v1/images/generations"
    payload.normalize_model_options(api_path)
    prepared_request = await _prepare_upstream_request(
        api_url=api_url,
        api_key=api_key,
        api_path=api_path,
        socks5_proxy=socks5_proxy,
    )

    pool = get_pool()
    memory_lease = upstream_memory_lease(
        upstream_task_memory_weight(payload.response_format)
    )
    await memory_lease.__aenter__()
    try:
        async with prepared_request.post(
            json=_build_image_params(payload),
        ) as resp:
            result, response_text = await parse_upstream_json_response(
                resp, api_path, None
            )

        data = validate_upstream_image_data(result.get("data", []), 1)
        if not data:
            raise UpstreamApiError(
                f"No image data in upstream response: {response_text[:200]}"
            )
        image_bytes = await extract_image_bytes(
            pool.get(timeout_kind=TIMEOUT_UPSTREAM),
            data[0],
            response_text[:200],
            config.MAX_FILE_SIZE_MB * 1024 * 1024,
        )
        if len(image_bytes) > config.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise UpstreamImageDownloadError(
                f"Image too large: {len(image_bytes)} bytes "
                f"(max {config.MAX_FILE_SIZE_MB * 1024 * 1024})"
            )
        detected_format = detect_image_format(image_bytes) or payload.output_format
        extension = DETECTED_FORMAT_EXTENSIONS.get(detected_format, "png")
        await run_image_operation(
            validate_generated_image_bytes,
            image_bytes,
            f"assistant-preview.{extension}",
            metric_name="validate_assistant_preview",
        )
        return image_bytes
    finally:
        await memory_lease.__aexit__(None, None, None)


async def call_image_edit_api(
    api_url: str,
    api_key: str,
    payload: EditRequest,
    image_sources: Sequence[ImageEditSource],
    api_preset_name: str | None = None,
    progress: ProgressCallback | None = None,
    socks5_proxy: str | None = None,
    *,
    stream: bool = False,
    partial_images: int = 2,
    preview: "PreviewCallback | None" = None,
    persist_gallery_entry: PersistGalleryEntry,
    mask_source: ImageEditSource | None = None,
    mask_coverage: float | None = None,
    prompt_guard: bool = False,
    image_id_factory: Callable[[int], str] | None = None,
) -> list[GalleryEntry]:
    if not image_sources:
        raise UpstreamApiError("At least one edit source image is required")

    use_streaming = bool(stream)
    api_path = "/v1/images/edits"
    payload.normalize_model_options(api_path)
    prepared_request = await _prepare_upstream_request(
        api_url=api_url,
        api_key=api_key,
        api_path=api_path,
        socks5_proxy=socks5_proxy,
        json_content_type=False,
    )
    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = build_gallery_metadata(
        payload,
        api_path,
        api_preset_name,
        mask_coverage=mask_coverage,
    )
    gallery_metadata["sent_prompt"] = sent_generation_prompt(payload, prompt_guard=prompt_guard)
    transform_image = None
    if mask_source is not None:
        if payload.paste_back is False:
            gallery_metadata["paste_back"] = "skipped:disabled"
            metrics.increment("image_jobs.paste_back.skipped.disabled")
        else:
            async def transform_image(image_bytes: bytes) -> PasteBackOutcome:
                return await run_image_operation(
                    paste_back_image,
                    image_bytes,
                    image_sources[0].temp_path,
                    mask_source.temp_path,
                    output_format=payload.output_format,
                    output_compression=payload.output_compression,
                    background=payload.background,
                    metric_name="paste_back_image",
                )

    if progress:
        progress("building_edit_form", "Building multipart edit request")
    image_files = []
    memory_lease = None
    try:
        form = aiohttp.FormData()
        image_field_name = "image" if len(image_sources) == 1 else "image[]"
        for source in image_sources:
            image_file = source.temp_path.open("rb")
            image_files.append(image_file)
            form.add_field(
                image_field_name,
                image_file,
                filename=source.filename or "image.png",
                content_type=source.content_type or "application/octet-stream",
            )
        if mask_source is not None:
            mask_file = mask_source.temp_path.open("rb")
            image_files.append(mask_file)
            form.add_field(
                "mask",
                mask_file,
                filename=mask_source.filename or "mask.png",
                content_type="image/png",
            )
        for key, value in _build_image_params(payload, prompt_guard=prompt_guard).items():
            form.add_field(key, str(value))
        if use_streaming:
            form.add_field("stream", "true")
            form.add_field("partial_images", str(max(1, min(3, int(partial_images or 2)))))

        pool = get_pool()
        memory_lease = upstream_memory_lease(
            upstream_task_memory_weight(payload.response_format)
            + (max(0, image_sources[0].width or 0) * max(0, image_sources[0].height or 0) * 12
               if transform_image is not None else 0)
        )
        await memory_lease.__aenter__()
        if progress:
            upload_message = (
                "Uploading source image and edit parameters"
                if len(image_sources) == 1
                else "Uploading source images and edit parameters"
            )
            if mask_source is not None:
                upload_message = (
                    "Uploading source image, mask and edit parameters"
                    if len(image_sources) == 1
                    else "Uploading source images, mask and edit parameters"
                )
            progress("uploading_edit_image", upload_message)
        upstream_started = time.monotonic()
        with observe_job_stage("upstream_wait"):
            async with prepared_request.post(
                data=form,
            ) as resp:
                if use_streaming:
                    data, raw_usage = await consume_streaming_image_response(
                        resp, api_path, progress, preview
                    )
                    record_upstream_usage(raw_usage)
                    response_text = ""
                else:
                    result, response_text = await parse_upstream_json_response(
                        resp, api_path, progress
                    )
                    record_upstream_usage(result.get("usage") if isinstance(result, dict) else None)

        gallery_metadata["upstream_duration_ms"] = max(0, round((time.monotonic() - upstream_started) * 1000))

        if not use_streaming:
            if progress:
                progress("extracting_edit_data", "Extracting edited image data array")
            raw_data = result.get("data", [])
            data = [
                {**item, **reported_image_fields(result)} if isinstance(item, dict) else item
                for item in raw_data
            ] if isinstance(raw_data, list) else raw_data
        data = validate_upstream_image_data(data, payload.n)
        if not data:
            raise UpstreamApiError(f"No image data in upstream response: {response_text[:200]}")

        response_preview = response_text[:200]
        del response_text
        if not use_streaming:
            del result
        download_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM)
        return await save_gallery_entries_from_upstream_data(
            download_session=download_session,
            data=data,
            response_preview=response_preview,
            payload=payload,
            format_extension=format_info["extension"],
            gallery_metadata=gallery_metadata,
            save_message="Saving edited images",
            progress=progress,
            persist_gallery_entry=persist_gallery_entry,
            transform_image=transform_image,
            prompt_guard=prompt_guard,
        )
    finally:
        if memory_lease is not None:
            await memory_lease.__aexit__(None, None, None)
        for image_file in image_files:
            image_file.close()
