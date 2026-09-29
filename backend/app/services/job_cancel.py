"""Cancel a queued or running image job, shared by the route and the agent."""

from ..core.errors import DomainError, NotFoundError
from ..repositories.image_jobs import (
    aggregate_image_job_units,
    cancel_generate_job_tx,
    release_edit_source_reservation,
)
from ..runtime.blocking import run_db_operation
from .job_events import publish_generate_job_row_async, resolve_generate_job_view
from .job_queue import cleanup_parent_edit_sources, trim_generate_jobs


async def cancel_image_job(job_id: str) -> None:
    job = await resolve_generate_job_view(job_id)
    if not job:
        raise NotFoundError("Generation job not found")
    if job.get("status") not in {"queued", "running"}:
        raise DomainError("Generation job already finished", status_code=409)

    aggregate = await run_db_operation(
        aggregate_image_job_units,
        job_id,
        metric_name="aggregate_cancelled_image_job",
    )
    cancel_message = (
        "Image edit job cancelled"
        if job.get("operation") == "edit"
        else "Generation job cancelled"
    )
    row, cancelled = await run_db_operation(
        cancel_generate_job_tx,
        job_id,
        cancel_message,
        metric_name="cancel_generate_job",
        critical=True,
    )
    if row is None:
        raise NotFoundError("Generation job not found")
    if not cancelled:
        raise DomainError("Generation job already finished", status_code=409)
    await publish_generate_job_row_async(row, dispatch_webhook=True)
    await run_db_operation(trim_generate_jobs, metric_name="trim_generate_jobs")

    if job.get("operation") == "edit":
        if int(aggregate.get("running_count") or 0) > 0:
            await run_db_operation(
                release_edit_source_reservation,
                job_id,
                metric_name="release_edit_source_reservation",
            )
        else:
            await run_db_operation(
                cleanup_parent_edit_sources,
                job_id,
                metric_name="cleanup_parent_edit_sources",
            )
