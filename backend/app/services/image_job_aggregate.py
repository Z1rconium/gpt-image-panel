"""Parent image-job aggregation from unit rows, and in-memory progress publishing."""

import logging
from datetime import datetime

from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.utils import beijing_now, utc_now
from ..repositories.image_jobs import finalize_parent_job_from_units, get_generate_job_with_unit_aggregate
from ..runtime.blocking import run_db_operation
from ..runtime.state import state
from .job_events import clear_generate_job_unit_previews, publish_generate_job, publish_generate_job_row_async, store_generate_job_async
from .job_queue import cleanup_parent_edit_sources, summarize_unit_failures, trim_generate_jobs

logger = logging.getLogger(__name__)


def _aggregate_image_job_duration(units: list[dict]) -> str | None:
    durations: list[tuple[float, str]] = []
    started_at: list[datetime] = []
    completed_at: list[datetime] = []
    for unit in units:
        duration = str(unit.get("duration") or "").strip()
        if duration.endswith("s"):
            try:
                seconds = float(duration[:-1])
            except ValueError:
                pass
            else:
                if seconds >= 0:
                    durations.append((seconds, duration))
        try:
            unit_started_at = datetime.fromisoformat(
                str(unit["started_at"]).replace("Z", "+00:00")
            )
            unit_completed_at = datetime.fromisoformat(
                str(unit["completed_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            continue
        started_at.append(unit_started_at)
        completed_at.append(unit_completed_at)

    if len(units) == 1 and durations:
        return durations[0][1]
    if started_at and completed_at:
        try:
            seconds = (max(completed_at) - min(started_at)).total_seconds()
        except TypeError:
            pass
        else:
            if seconds >= 0:
                return f"{seconds:.2f}s"
    return max(durations, default=None, key=lambda item: item[0])[1] if durations else None


def derive_parent_update(
    parent: dict,
    aggregate: dict,
    *,
    operation: str,
) -> dict | None:
    """Pure mapping from unit aggregate to the parent job update.

    Kept side-effect free so the terminal decision can be re-evaluated inside
    the repository write transaction (`finalize_parent_job_from_units`) and
    unit-tested in isolation.
    """
    total = int(aggregate.get("total") or parent.get("n") or 1)
    completed = int(aggregate.get("completed") or 0)
    success_count = int(aggregate.get("success_count") or 0)
    failure_count = int(aggregate.get("failure_count") or 0)
    running_count = int(aggregate.get("running_count") or 0)
    queued_count = int(aggregate.get("queued_count") or 0)
    count_update = {
        "completed_count": completed,
        "success_count": success_count,
        "failure_count": failure_count,
        "unit_statuses": {str(unit.get("unit_index", index)): unit["status"] for index, unit in enumerate(aggregate.get("units", []))},
    }
    usage_cost_update = {
        "usage": aggregate.get("usage"),
        "cost": aggregate.get("cost"),
    }

    if aggregate.get("all_terminal"):
        images = aggregate.get("images") or []
        failures = aggregate.get("failures") or []
        first_image = images[0] if images else {}
        completed_at = str(first_image.get("completed_at") or beijing_now())
        terminal_update = {
            "duration": _aggregate_image_job_duration(aggregate.get("units") or []),
        }
        if images:
            message = (
                "Image edit completed"
                if operation == "edit"
                else "Image generation completed"
            )
            if failures:
                message = f"Generated {len(images)} of {total} requested images; {len(failures)} failed"
            update = {
                "status": "partial_failure" if failures else "success",
                "stage": "completed_with_failures" if failures else "completed",
                "message": message,
                "operation": operation,
                "image_id": first_image.get("image_id"),
                "image_url": first_image.get("image_url"),
                "images": images,
                "prompt": parent.get("prompt"),
                "size": parent.get("size"),
                "image_width": first_image.get("image_width"),
                "image_height": first_image.get("image_height"),
                "model": parent.get("model"),
                "quality": parent.get("quality"),
                "output_format": parent.get("output_format"),
                "output_compression": parent.get("output_compression"),
                "background": parent.get("background"),
                "response_format": parent.get("response_format"),
                "n": parent.get("n"),
                "api_path": parent.get("api_path"),
                "api_preset_name": parent.get("api_preset_name"),
                "stage_timings": aggregate.get("stage_timings") or {},
                "completed_at": completed_at,
                **terminal_update,
                **count_update,
                **usage_cost_update,
            }
            if failures:
                update["error"] = summarize_unit_failures(failures, total, operation)
            return update
        if aggregate.get("all_cancelled"):
            cancel_message = (
                "Image edit job cancelled"
                if operation == "edit"
                else "Generation job cancelled"
            )
            return {
                "status": "cancelled",
                "stage": "cancelled",
                "message": cancel_message,
                "operation": operation,
                "completed_at": completed_at,
                "error": cancel_message,
                **terminal_update,
                **count_update,
                **usage_cost_update,
            }
        failures = failures or aggregate.get("units") or []
        status = (
            "upstream_error"
            if failures and all(unit.get("status") == "upstream_error" for unit in failures)
            else "error"
        )
        error_message = summarize_unit_failures(failures, total, operation)
        return {
            "status": status,
            "stage": "generation_failed" if operation == "generation" else "edit_failed",
            "message": error_message,
            "operation": operation,
            "completed_at": completed_at,
            "error": error_message,
            "stage_timings": aggregate.get("stage_timings") or {},
            **terminal_update,
            **count_update,
            **usage_cost_update,
        }

    if running_count > 0 or completed > 0:
        stage = "waiting_for_api"
        images = aggregate.get("images") or []
        first_image = images[0] if images else {}
        message = (
            f"Editing images ({completed}/{total} completed)"
            if operation == "edit"
            else f"Generating images ({completed}/{total} completed)"
        )
        return {
            "status": "running",
            "stage": stage,
            "message": message,
            "operation": operation,
            "started_at": parent.get("started_at") or utc_now(),
            "image_id": first_image.get("image_id"),
            "image_url": first_image.get("image_url"),
            "images": images,
            "image_width": first_image.get("image_width"),
            "image_height": first_image.get("image_height"),
            **count_update,
            **usage_cost_update,
        }

    if queued_count > 0:
        return {
            "status": "queued",
            "stage": "queued",
            "message": parent.get("message") or "Queued image generation",
            "operation": operation,
            **count_update,
        }
    return None


async def aggregate_parent_image_job(
    parent_job_id: str,
    *,
    force_publish: bool = False,
) -> dict | None:
    parent, aggregate = await run_db_operation(
        get_generate_job_with_unit_aggregate,
        parent_job_id,
        metric_name="aggregate_parent_image_job",
    )
    if not parent:
        return None
    for unit in aggregate.get("units", []):
        if unit["status"] not in ACTIVE_GENERATE_JOB_STATUSES:
            clear_generate_job_unit_previews(parent_job_id, int(unit["unit_index"]))
    operation = str(parent.get("operation") or "generation")

    if aggregate.get("all_terminal"):
        # Unit states only move forward, so a job observed as all-terminal here
        # is still all-terminal when re-derived inside the transaction.
        def derive(parent_row: dict, aggregate_row: dict) -> dict | None:
            return derive_parent_update(
                parent_row,
                aggregate_row,
                operation=str(parent_row.get("operation") or operation),
            )

        row, written = await run_db_operation(
            finalize_parent_job_from_units,
            parent_job_id,
            derive=derive,
            metric_name="finalize_parent_image_job",
            critical=True,
        )
        if not row:
            return None
        job = await publish_generate_job_row_async(row, dispatch_webhook=written)
        await run_db_operation(
            trim_generate_jobs,
            metric_name="trim_generate_jobs",
        )
        if operation == "edit":
            await run_db_operation(
                cleanup_parent_edit_sources,
                parent_job_id,
                metric_name="cleanup_parent_edit_sources",
            )
        return job

    update = derive_parent_update(parent, aggregate, operation=operation)
    if update is None:
        return parent
    return await store_generate_job_async(
        parent_job_id,
        update,
        persist=force_publish,
    )


def set_generate_job_progress(
    job_id: str,
    stage: str,
    message: str,
    operation: str,
):
    job = state.generate_jobs.get(job_id)
    if not job:
        return

    updated = {
        **job,
        "status": "running",
        "stage": stage,
        "message": message,
        "operation": operation,
        "updated_at": utc_now(),
    }
    state.generate_jobs[job_id] = updated
    publish_generate_job(updated, list_debounce=True, list_reconcile=False)
