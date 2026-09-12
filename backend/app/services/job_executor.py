"""Claimed image unit execution and parent-job aggregation."""

import asyncio
import base64
import logging
import time
from datetime import datetime

from ..api.app_state import app
from ..api.presets import (
    get_effective_preset_api_key,
    get_exception_message,
    get_upstream_socks5_proxy,
)
from ..core import settings as config
from ..core import validators as ssrf
from ..core.observability import (
    JobStageTimer,
    UsageSink,
    metrics,
    use_job_stage_timer,
    use_usage_sink,
)
from ..core.utils import beijing_now, utc_now
from ..integrations.upstream import generation as proxy
from ..repositories.gallery.mutations import update_gallery_entry
from ..repositories.image_jobs import (
    complete_image_job_unit,
    fail_image_job_unit,
    get_generate_job,
    get_generate_job_with_unit_aggregate,
    renew_image_job_unit_lease,
    update_image_job_unit_progress,
)
from . import image_cost
from .job_events import (
    publish_generate_job,
    publish_generate_job_preview,
    store_generate_job_async,
)
from .blocking import run_db_operation
from .job_queue import (
    cleanup_parent_edit_sources,
    edit_source_from_payload,
    gallery_entry_job_result,
    get_preset_for_unit,
    kick_thumbnail_dispatcher,
    rebuild_request,
    summarize_unit_failures,
    trim_generate_jobs,
)

logger = logging.getLogger(__name__)
# Cadence for retrying a lease renewal that failed with a DB error, while the
# locally tracked lease is still valid.
IMAGE_UNIT_LEASE_RENEW_RETRY_SECONDS = 5.0


class UnitLeaseLostError(Exception):
    """Raised when this worker's claim token no longer owns the image unit.

    Ownership has moved to a newer claim (or the session was interrupted), so
    the executor must stop the in-flight upstream request and must not write a
    terminal state for the unit.
    """


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
    total = int(aggregate.get("total") or parent.get("n") or 1)
    completed = int(aggregate.get("completed") or 0)
    success_count = int(aggregate.get("success_count") or 0)
    failure_count = int(aggregate.get("failure_count") or 0)
    running_count = int(aggregate.get("running_count") or 0)
    queued_count = int(aggregate.get("queued_count") or 0)
    operation = str(parent.get("operation") or "generation")
    count_update = {
        "completed_count": completed,
        "success_count": success_count,
        "failure_count": failure_count,
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
        elif aggregate.get("all_cancelled"):
            cancel_message = (
                "Image edit job cancelled"
                if operation == "edit"
                else "Generation job cancelled"
            )
            update = {
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
        else:
            failures = failures or aggregate.get("units") or []
            status = (
                "upstream_error"
                if failures and all(unit.get("status") == "upstream_error" for unit in failures)
                else "error"
            )
            error_message = summarize_unit_failures(failures, total, operation)
            update = {
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
        job = await store_generate_job_async(parent_job_id, update)
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

    if running_count > 0 or completed > 0:
        stage = "waiting_for_api"
        images = aggregate.get("images") or []
        first_image = images[0] if images else {}
        message = (
            f"Editing images ({completed}/{total} completed)"
            if operation == "edit"
            else f"Generating images ({completed}/{total} completed)"
        )
        return await store_generate_job_async(
            parent_job_id,
            {
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
            },
            persist=force_publish,
        )

    if queued_count > 0:
        return await store_generate_job_async(
            parent_job_id,
            {
                "status": "queued",
                "stage": "queued",
                "message": parent.get("message") or "Queued image generation",
                "operation": operation,
                **count_update,
            },
            persist=force_publish,
        )
    return parent


def set_generate_job_progress(
    job_id: str,
    stage: str,
    message: str,
    operation: str,
):
    job = app.state.generate_jobs.get(job_id)
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
    app.state.generate_jobs[job_id] = updated
    publish_generate_job(updated, list_debounce=True, list_reconcile=False)


def image_unit_lease_expires_at() -> str:
    return datetime_from_monotonic_delta(config.IMAGE_JOB_UNIT_LEASE_SECONDS)


def image_unit_lease_renew_interval() -> float:
    """Renewal cadence for an in-flight image unit.

    Trusts the configured value when it is safely below `lease/2`; otherwise
    falls back to `lease/4` so a renewal can always land before expiry.
    """
    lease = float(config.IMAGE_JOB_UNIT_LEASE_SECONDS)
    renew = float(config.IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS)
    if renew <= 0 or renew >= lease / 2:
        renew = lease / 4
    return max(0.1, renew)


def datetime_from_monotonic_delta(seconds: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=max(1.0, seconds))).isoformat()


async def run_claimed_image_unit(unit: dict, worker_id: str):
    unit_id = str(unit["unit_id"])
    parent_job_id = str(unit["parent_job_id"])
    operation = str(unit.get("operation") or "generation")
    claim_token = str(unit.get("claim_token") or "")
    stage_timer = JobStageTimer()
    usage_sink = UsageSink()
    started_at = time.monotonic()
    parent = await run_db_operation(
        get_generate_job,
        parent_job_id,
        metric_name="get_generate_job_for_unit",
    ) or {}
    req = rebuild_request(operation, unit.get("request") or {})
    api_path = str(
        unit.get("api_path")
        or parent.get("api_path")
        or "/v1/images/generations"
    )
    api_preset_name = str(
        unit.get("api_preset_name") or parent.get("api_preset_name") or ""
    )
    preset = await run_db_operation(
        get_preset_for_unit,
        unit,
        metric_name="get_preset_for_image_unit",
    )
    if not preset:
        raise RuntimeError("API preset not found for image unit")
    api_url = ssrf.normalize_upstream_base_url(
        str(preset.get("api_url") or "").rstrip("/")
    )
    api_key = get_effective_preset_api_key(preset)
    socks5_proxy = get_upstream_socks5_proxy()

    progress_pending: tuple[str, str] | None = None
    progress_task: asyncio.Task | None = None
    lease_task: asyncio.Task | None = None
    upstream_task: asyncio.Task | None = None
    last_progress_persist_at = 0.0
    lease_lost = asyncio.Event()
    # Local view of when the SQLite lease expires. Every successful write that
    # sets `claim_expires_at` (start progress, coalesced progress, renewal)
    # pushes it forward; the renewal loop uses it to decide how long a failing
    # renewal may keep retrying before the unit must be abandoned.
    lease_deadline = time.monotonic() + float(config.IMAGE_JOB_UNIT_LEASE_SECONDS)

    def note_lease_extended(anchor: float | None = None):
        """Record a lease extension, anchored at the time the new expiry was
        computed (before the DB write) so the local deadline never runs ahead
        of the value actually stored in SQLite."""
        nonlocal lease_deadline
        anchored_at = time.monotonic() if anchor is None else anchor
        lease_deadline = anchored_at + float(config.IMAGE_JOB_UNIT_LEASE_SECONDS)

    def raise_if_lease_lost():
        if lease_lost.is_set():
            raise UnitLeaseLostError()

    def mark_lease_lost():
        if lease_lost.is_set():
            return
        lease_lost.set()
        metrics.increment("image_jobs.lease_lost")

    async def abort_upstream():
        """Cancel the in-flight upstream call and wait for it to unwind.

        `asyncio.wait` does not cancel its members, so an outer cancellation
        (graceful shutdown) or a lost lease must stop the upstream task
        explicitly; otherwise the request keeps running and its late progress
        writes are misreported as lease loss.
        """
        task = upstream_task
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def renew_lease_loop():
        """Periodically extend the unit lease while the upstream call runs.

        A `False` return (or a fencing check failure from any other write
        path) means ownership moved elsewhere, so signal lease loss right away.
        A DB error is different: the lease is still ours until `lease_deadline`,
        so retry on a short cadence and only give up once another retry could
        no longer land before expiry. Aborting an expensive upstream call over
        one transient SQLite stall would burn an attempt for nothing.
        """
        renew_interval = image_unit_lease_renew_interval()
        retry_interval = min(renew_interval, IMAGE_UNIT_LEASE_RENEW_RETRY_SECONDS)
        delay = renew_interval
        while True:
            await asyncio.sleep(delay)
            if lease_lost.is_set():
                return
            renew_started_at = time.monotonic()
            try:
                renewed = await run_db_operation(
                    renew_image_job_unit_lease,
                    unit_id,
                    claim_token=claim_token,
                    claim_expires_at=image_unit_lease_expires_at(),
                    metric_name="renew_image_job_unit_lease",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                remaining = lease_deadline - time.monotonic()
                if remaining <= retry_interval:
                    logger.exception(
                        "Image unit lease renewal failed and the lease is about "
                        "to expire, abandoning unit: unit_id=%s parent_job_id=%s "
                        "worker_id=%s remaining=%.1fs",
                        unit_id,
                        parent_job_id,
                        worker_id,
                        remaining,
                    )
                    mark_lease_lost()
                    return
                logger.warning(
                    "Image unit lease renewal failed, retrying in %.1fs "
                    "(lease valid for %.1fs): unit_id=%s parent_job_id=%s worker_id=%s",
                    retry_interval,
                    remaining,
                    unit_id,
                    parent_job_id,
                    worker_id,
                    exc_info=True,
                )
                metrics.increment("image_jobs.lease_renew_retry")
                delay = retry_interval
                continue
            if renewed:
                note_lease_extended(renew_started_at)
                metrics.increment("image_jobs.lease_renewed")
                delay = renew_interval
                continue
            logger.warning(
                "Image unit lease lost: unit_id=%s parent_job_id=%s worker_id=%s",
                unit_id,
                parent_job_id,
                worker_id,
            )
            mark_lease_lost()
            return

    async def persist_progress_updates():
        nonlocal progress_pending, progress_task, last_progress_persist_at
        try:
            while progress_pending is not None:
                delay = config.IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS - (
                    time.monotonic() - last_progress_persist_at
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                stage, message = progress_pending
                progress_pending = None
                persist_started_at = time.monotonic()
                updated = await run_db_operation(
                    update_image_job_unit_progress,
                    unit_id,
                    claim_token=claim_token,
                    stage=stage,
                    message=message,
                    claim_expires_at=image_unit_lease_expires_at(),
                    metric_name="persist_image_job_progress",
                )
                if updated is None:
                    logger.warning(
                        "Image unit progress rejected, lease lost: "
                        "unit_id=%s parent_job_id=%s worker_id=%s",
                        unit_id,
                        parent_job_id,
                        worker_id,
                    )
                    mark_lease_lost()
                    return
                note_lease_extended(persist_started_at)
                last_progress_persist_at = time.monotonic()
                await aggregate_parent_image_job(parent_job_id)
        finally:
            progress_task = None
            if progress_pending is not None and not lease_lost.is_set():
                progress_task = asyncio.create_task(persist_progress_updates())

    def progress(stage: str, message: str):
        set_generate_job_progress(parent_job_id, stage, message, operation)
        if lease_lost.is_set():
            return
        nonlocal progress_pending, progress_task
        progress_pending = (stage, message)
        if progress_task is None or progress_task.done():
            progress_task = asyncio.create_task(persist_progress_updates())

    async def flush_progress_updates():
        nonlocal progress_task
        task = progress_task
        if task is not None:
            await asyncio.gather(task, return_exceptions=False)
        while progress_task is not None:
            task = progress_task
            await asyncio.gather(task, return_exceptions=False)

    async def flush_progress_before_terminal(*, suppress_cancelled: bool):
        """Drain pending progress writes before a terminal unit write.

        Progress persistence must finish before the unit leaves `running`;
        otherwise a late progress write after the terminal write sees rowcount 0
        and is misread as a lost lease. In the graceful-cancellation path the
        nested CancelledError is expected and suppressed so the cancelled
        terminal state can still be written; elsewhere it is re-raised so we do
        not mask an outer cancellation.
        """
        try:
            await flush_progress_updates()
        except asyncio.CancelledError:
            if not suppress_cancelled:
                raise
            logger.debug(
                "Progress flush interrupted before terminal write: unit_id=%s",
                unit_id,
            )
        except Exception:
            logger.exception(
                "Progress flush failed before terminal write: unit_id=%s",
                unit_id,
            )

    async def parent_was_cancelled() -> bool:
        current = await run_db_operation(
            get_generate_job,
            parent_job_id,
            metric_name="check_generate_job_cancelled",
        )
        return bool(current and current.get("status") == "cancelled")

    try:
        if await parent_was_cancelled():
            raise asyncio.CancelledError()
        start_stage = "starting_edit" if operation == "edit" else "starting_generation"
        start_message = (
            "Starting image edit" if operation == "edit" else "Starting image generation"
        )
        start_persist_at = time.monotonic()
        if (
            await run_db_operation(
                update_image_job_unit_progress,
                unit_id,
                claim_token=claim_token,
                stage=start_stage,
                message=start_message,
                claim_expires_at=image_unit_lease_expires_at(),
                metric_name="start_image_job_unit",
            )
            is None
        ):
            raise UnitLeaseLostError()
        note_lease_extended(start_persist_at)
        await store_generate_job_async(
            parent_job_id,
            {
                "status": "running",
                "stage": start_stage,
                "message": start_message,
                "operation": operation,
                "started_at": parent.get("started_at") or utc_now(),
            },
        )
        if int(parent.get("n") or 1) > 1:
            await aggregate_parent_image_job(parent_job_id, force_publish=True)
        metrics.increment(f"image_jobs.{operation}.started")

        stream_kwargs: dict = {}
        if getattr(req, "stream", False):
            preview_sequence = 0
            unit_index = int(unit.get("unit_index") or 0)

            def on_preview(partial_image_index: int, mime_type: str, image_bytes: bytes) -> None:
                nonlocal preview_sequence
                preview_sequence += 1
                data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
                publish_generate_job_preview(
                    parent_job_id,
                    {
                        "job_id": parent_job_id,
                        "unit_index": unit_index,
                        "partial_image_index": partial_image_index,
                        "sequence": preview_sequence,
                        "mime_type": mime_type,
                        "data_url": data_url,
                    },
                )

            stream_kwargs = {
                "stream": True,
                "partial_images": getattr(req, "partial_images", 2),
                "preview": on_preview,
            }
            metrics.increment("image_job.streaming_requested")

        async def run_upstream() -> list:
            with use_job_stage_timer(stage_timer), use_usage_sink(usage_sink):
                if operation == "edit":
                    image_sources = [
                        edit_source_from_payload(source)
                        for source in unit.get("edit_sources") or []
                    ]
                    if not image_sources:
                        raise proxy.UpstreamApiError(
                            "At least one edit source image is required"
                        )
                    return await proxy.call_image_edit_api(
                        api_url,
                        api_key,
                        req,  # type: ignore[arg-type]
                        image_sources,
                        api_preset_name,
                        progress,
                        socks5_proxy=socks5_proxy,
                        **stream_kwargs,
                    )
                return await proxy.call_image_generation_api(
                    api_url,
                    api_key,
                    api_path,
                    req,  # type: ignore[arg-type]
                    api_preset_name,
                    progress,
                    socks5_proxy=socks5_proxy,
                    **stream_kwargs,
                )

        lease_task = asyncio.create_task(renew_lease_loop())
        upstream_task = asyncio.create_task(run_upstream())
        await asyncio.wait(
            {upstream_task, lease_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if lease_lost.is_set() or lease_task.done():
            if lease_task.done() and not lease_task.cancelled():
                lease_task.exception()
            mark_lease_lost()
            await abort_upstream()
            raise UnitLeaseLostError()
        # Lease loop keeps running until upstream completes; cancel it.
        lease_task.cancel()
        entries = await upstream_task
        if not entries:
            raise proxy.UpstreamApiError("No image data in upstream response")

        await flush_progress_updates()
        raise_if_lease_lost()
        duration_seconds = time.monotonic() - started_at
        duration = f"{duration_seconds:.2f}s"
        completed_at = beijing_now()

        def update_entries():
            return [
                update_gallery_entry(
                    entry.id,
                    {
                        "duration": duration,
                        "completed_at": completed_at,
                        "n": parent.get("n") or req.n,
                    },
                )
                or entry
                for entry in entries
            ]

        updated_entries = await run_db_operation(
            update_entries,
            metric_name="finalize_gallery_entries",
        )
        kick_thumbnail_dispatcher()
        result_images = [gallery_entry_job_result(entry) for entry in updated_entries]
        stage_timings = stage_timer.snapshot()
        usage = image_cost.normalize_usage(usage_sink.raw_usage)
        cost = image_cost.estimate_image_cost(req.model, usage)
        metrics.increment(f"image_jobs.{operation}.succeeded")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
        if usage is None:
            metrics.increment("image_job.usage_missing")
        if not cost.get("complete"):
            metrics.increment("image_job.cost_unknown_rate")
        if await parent_was_cancelled():
            await run_db_operation(
                fail_image_job_unit,
                unit_id,
                claim_token=claim_token,
                status="cancelled",
                stage="cancelled",
                message="Generation job cancelled",
                error="Generation job cancelled",
                stage_timings=stage_timings,
                duration=duration,
                completed_at=utc_now(),
                usage=usage,
                cost=cost,
                metric_name="cancel_completed_image_job_unit",
            )
            return
        if (
            await run_db_operation(
                complete_image_job_unit,
                unit_id,
                claim_token=claim_token,
                result={"images": result_images},
                stage_timings=stage_timings,
                duration=duration,
                completed_at=completed_at,
                usage=usage,
                cost=cost,
                metric_name="complete_image_job_unit",
            )
            is None
        ):
            raise UnitLeaseLostError()
    except asyncio.CancelledError:
        # Stop the upstream request first: `asyncio.wait` leaves it running,
        # and any progress it reports after the terminal write below would be
        # rejected by fencing and misread as a lost lease.
        await abort_upstream()
        duration_seconds = time.monotonic() - started_at
        stage_timings = stage_timer.snapshot()
        usage = image_cost.normalize_usage(usage_sink.raw_usage)
        cost = image_cost.estimate_image_cost(req.model, usage) if usage else None
        metrics.increment(f"image_jobs.{operation}.cancelled")
        await flush_progress_before_terminal(suppress_cancelled=True)
        await run_db_operation(
            fail_image_job_unit,
            unit_id,
            claim_token=claim_token,
            status="cancelled",
            stage="cancelled",
            message="Generation job cancelled",
            error="Generation job cancelled",
            stage_timings=stage_timings,
            duration=f"{duration_seconds:.2f}s",
            completed_at=utc_now(),
            usage=usage,
            cost=cost,
            metric_name="cancel_image_job_unit",
        )
    except UnitLeaseLostError:
        logger.warning(
            "Image unit lease lost, aborting without terminal write: "
            "unit_id=%s parent_job_id=%s worker_id=%s",
            unit_id,
            parent_job_id,
            worker_id,
        )
    except Exception as error:
        error_message = get_exception_message(error)
        status = (
            "upstream_error" if isinstance(error, proxy.UpstreamApiError) else "error"
        )
        duration_seconds = time.monotonic() - started_at
        stage_timings = stage_timer.snapshot()
        usage = image_cost.normalize_usage(usage_sink.raw_usage)
        cost = image_cost.estimate_image_cost(req.model, usage) if usage else None
        cancelled = await parent_was_cancelled()
        if not cancelled:
            metrics.increment(f"image_jobs.{operation}.failed")
            metrics.observe_ms("image_job.duration", duration_seconds * 1000)
            metrics.observe_job_stage_timings(stage_timings)
            logger.exception(
                "Image unit failed: unit_id=%s parent_job_id=%s worker_id=%s error_type=%s",
                unit_id,
                parent_job_id,
                worker_id,
                error.__class__.__name__,
            )
        await flush_progress_before_terminal(suppress_cancelled=False)
        if lease_lost.is_set():
            logger.warning(
                "Image unit lease lost before terminal write, skipping: "
                "unit_id=%s parent_job_id=%s worker_id=%s",
                unit_id,
                parent_job_id,
                worker_id,
            )
            return
        await run_db_operation(
            fail_image_job_unit,
            unit_id,
            claim_token=claim_token,
            status="cancelled" if cancelled else status,
            stage=(
                "cancelled"
                if cancelled
                else "generation_failed" if operation == "generation" else "edit_failed"
            ),
            message="Generation job cancelled" if cancelled else error_message,
            error="Generation job cancelled" if cancelled else error_message,
            stage_timings=stage_timings,
            duration=f"{duration_seconds:.2f}s",
            completed_at=utc_now(),
            usage=usage,
            cost=cost,
            metric_name="fail_image_job_unit",
        )
    finally:
        await abort_upstream()
        if lease_task is not None:
            if not lease_task.done():
                lease_task.cancel()
            elif not lease_task.cancelled():
                lease_task.exception()
        await flush_progress_updates()
        if not lease_lost.is_set():
            await aggregate_parent_image_job(parent_job_id, force_publish=True)
