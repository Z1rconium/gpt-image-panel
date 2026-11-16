"""Cancel a queued or running image job, shared by the route and the agent."""

from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.errors import DomainError, NotFoundError
from .image_job_terminal import CancelJobOutcome, settle_terminal
from .job_persistence import resolve_generate_job_view


async def cancel_image_job(job_id: str) -> None:
    job = await resolve_generate_job_view(job_id)
    if not job:
        raise NotFoundError("Generation job not found")
    if job.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
        raise DomainError("Generation job already finished", status_code=409)

    operation = str(job.get("operation") or "generation")
    cancel_message = (
        "Image edit job cancelled"
        if operation == "edit"
        else "Generation job cancelled"
    )
    await settle_terminal(
        job_id,
        CancelJobOutcome(message=cancel_message),
        dispatch_webhook=True,
        operation=operation,
        require_transition=True,
    )
