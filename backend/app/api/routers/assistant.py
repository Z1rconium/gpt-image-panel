"""Assistant HTTP request and response mapping."""

from typing import Literal

from fastapi import APIRouter, Body, File, Form, Request, UploadFile

from ..responses import streaming_response
from ..uploads import read_image_upload, resolve_upload_content_type
from ...core import security as auth
from ...core.image_models import MAX_PROMPT_CHARS
from ...schemas.settings import AIAssistantSettingsRequest
from ...schemas.assistant import AssistantImagePromptOptimizeResponse, AssistantImagePromptResponse

from ...schemas.assistant import (
    AssistantEditPlanResponse,
    AssistantGalleryBatchJobStatus,
    AssistantGalleryImageResponse,
    AssistantGalleryMetadataResponse,
    AssistantHealthResponse,
    AssistantImagePromptResponse,
    AssistantImagePromptOptimizeResponse,
    AssistantJobDiagnoseResponse,
    AssistantPromptCheckResponse,
    AssistantPromptRewriteResponse,
    AssistantPromptVariantsResponse,
    AssistantRecommendParamsResponse,
)
from ...services import assistant_batch, assistant_text, assistant_vision

router = APIRouter()

router.add_api_route("/api/assistant/prompt/rewrite", assistant_text.rewrite_prompt, methods=["POST"], response_model=AssistantPromptRewriteResponse)
router.add_api_route("/api/assistant/prompt/check", assistant_text.check_prompt, methods=["POST"], response_model=AssistantPromptCheckResponse)
router.add_api_route("/api/assistant/prompt/variants", assistant_text.prompt_variants, methods=["POST"], response_model=AssistantPromptVariantsResponse)
router.add_api_route("/api/assistant/generate/recommend-params", assistant_text.recommend_generate_params, methods=["POST"], response_model=AssistantRecommendParamsResponse)
router.add_api_route("/api/assistant/jobs/{job_id}/diagnose", assistant_text.diagnose_job, methods=["POST"], response_model=AssistantJobDiagnoseResponse)
router.add_api_route("/api/assistant/edit/plan", assistant_text.plan_edit, methods=["POST"], response_model=AssistantEditPlanResponse)
router.add_api_route("/api/assistant/gallery/{image_id}/metadata", assistant_vision.get_gallery_metadata, methods=["GET"], response_model=AssistantGalleryMetadataResponse)
router.add_api_route("/api/assistant/gallery/batch/analyze", assistant_batch.batch_analyze_gallery, methods=["POST"], response_model=AssistantGalleryBatchJobStatus, status_code=202)
router.add_api_route("/api/assistant/gallery/batch/analyze/{job_id}", assistant_batch.get_batch_analyze_job, methods=["GET"], response_model=AssistantGalleryBatchJobStatus)
router.add_api_route("/api/assistant/gallery/{image_id}/describe", assistant_vision.describe_gallery_image, methods=["POST"], response_model=AssistantGalleryImageResponse)
router.add_api_route("/api/assistant/gallery/{image_id}/prompt", assistant_vision.prompt_gallery_image, methods=["POST"], response_model=AssistantGalleryImageResponse)
router.add_api_route("/api/assistant/gallery/{image_id}/analyze", assistant_vision.analyze_gallery_image, methods=["POST"], response_model=AssistantGalleryImageResponse)


@router.post("/api/assistant/health", response_model=AssistantHealthResponse)
async def assistant_health(req: AIAssistantSettingsRequest | None = Body(default=None)):
    return await assistant_text.assistant_health(req)


@router.post("/api/assistant/image/prompt", response_model=AssistantImagePromptResponse)
async def assistant_image_prompt(
    image: UploadFile = File(...),
    target_language: Literal["en", "zh-CN"] = Form("en"),
):
    image_bytes = await read_image_upload(image)
    return await assistant_vision.prompt_from_uploaded_image(
        image_bytes=image_bytes,
        filename=image.filename or "image",
        content_type=resolve_upload_content_type(image),
        target_language=target_language,
    )


@router.post(
    "/api/assistant/image/prompt/optimize",
    response_model=AssistantImagePromptOptimizeResponse,
)
async def assistant_optimize_image_prompt(
    image: UploadFile = File(...),
    prompt: str = Form(..., min_length=1, max_length=MAX_PROMPT_CHARS),
    target_language: Literal["en", "zh-CN"] = Form("en"),
):
    image_bytes = await read_image_upload(image)
    return await assistant_vision.optimize_uploaded_image_prompt(
        image_bytes=image_bytes,
        filename=image.filename or "image",
        content_type=resolve_upload_content_type(image),
        prompt=prompt,
        target_language=target_language,
    )


@router.get("/api/assistant/gallery/batch/analyze/{job_id}/events")
async def stream_assistant_batch_analyze_job(job_id: str, request: Request):
    body = await assistant_batch.stream_batch_analyze_job(
        job_id,
        client_ip=auth.get_client_ip(request),
        is_disconnected=request.is_disconnected,
    )
    return streaming_response(body)
