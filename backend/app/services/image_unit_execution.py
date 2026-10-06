"""Own the preparation, recovery and terminal writes of one claimed image unit."""

import asyncio
import base64
import logging
import time
import traceback

from .presets import (
    get_effective_preset_api_key,
    get_exception_message,
    get_upstream_socks5_proxy,
)
from ..core import validators as ssrf
from ..core.api_paths import (
    PROVIDER_KIND_ASYNC_JSON,
    normalize_prompt_guard,
    normalize_provider_kind,
)
from ..core.diagnostics import UnitDiagnostics
from ..core.redaction import redact_sensitive_text
from ..core.secrets import same_origin
from ..core.media import generate_stable_image_id, use_image_id_factory
from ..core.observability import (
    JobStageTimer,
    UsageSink,
    metrics,
    use_job_stage_timer,
    use_usage_sink,
)
from ..core.utils import beijing_now, utc_now
from ..integrations.upstream import generation as proxy
from ..repositories.gallery.mutations import add_to_gallery_async, update_gallery_entry
from ..repositories.image_jobs import (
    complete_image_job_unit,
    fail_image_job_unit,
    get_generate_job,
    get_image_job_unit,
    update_image_job_unit_progress,
    write_image_job_unit_remote,
)
from ..schemas.provider import resolve_provider_config
from ..core import image_cost
from .job_events import (
    publish_generate_job_preview,
    publish_generate_job_row_async,
    store_generate_job_async,
)
from ..runtime.blocking import run_db_operation
from .image_job_aggregate import aggregate_parent_image_job
from .image_unit_session import (
    ImageUnitSession,
    UnitLeaseLostError,
    image_unit_lease_expires_at,
)
from .job_queue import (
    edit_source_from_payload,
    gallery_entry_job_result,
    get_preset_for_unit,
    kick_thumbnail_dispatcher,
    rebuild_request,
)

logger = logging.getLogger(__name__)


class ImageUnitExecutionContext:
    """Execution state; lease and progress tasks belong to ImageUnitSession."""

    def __init__(self, unit: dict, worker_id: str):
        self.unit = unit
        self.worker_id = worker_id
        self.unit_id = str(self.unit["unit_id"])
        self.parent_job_id = str(self.unit["parent_job_id"])
        self.operation = str(self.unit.get("operation") or "generation")
        self.claim_token = str(self.unit.get("claim_token") or "")
        self.stage_timer = JobStageTimer()
        self.usage_sink = UsageSink()
        self.started_at = time.monotonic()
        self.req = rebuild_request(self.operation, self.unit.get("request") or {})
        self.session = ImageUnitSession(
            unit_id=self.unit_id,
            parent_job_id=self.parent_job_id,
            operation=self.operation,
            claim_token=self.claim_token,
            worker_id=self.worker_id,
        )
        self.parent = {}
        self.api_key = ""
        self.socks5_proxy = ""
        self.async_provider = False
        self.diagnostics = None

    async def prepare(self):
        self.parent = await run_db_operation(
            get_generate_job,
            self.parent_job_id,
            metric_name="get_generate_job_for_unit",
        ) or {}
        self.api_path = str(
            self.unit.get("api_path")
            or self.parent.get("api_path")
            or "/v1/images/generations"
        )
        self.api_preset_name = str(
            self.unit.get("api_preset_name") or self.parent.get("api_preset_name") or ""
        )
        self.preset = await run_db_operation(
            get_preset_for_unit,
            self.unit,
            metric_name="get_preset_for_image_unit",
        )
        if not self.preset:
            raise RuntimeError("API preset not found for image unit")
        snapshot = (self.unit.get("request") or {}).get("_provider_snapshot")
        self.snapshot_origin_changed = False
        if isinstance(snapshot, dict):
            self.snapshot_origin_changed = not same_origin(
                str(snapshot.get("api_url") or ""), str(self.preset.get("api_url") or ""),
            )
            # Credentials remain live and are never persisted in the snapshot.
            # Do not send credentials edited for another origin to the old one.
            self.preset = {**self.preset, **snapshot}
        self.api_url = ssrf.normalize_upstream_base_url(
            str(self.preset.get("api_url") or "").rstrip("/")
        )
        self.api_key = get_effective_preset_api_key(self.preset)
        self.prompt_guard = normalize_prompt_guard(self.preset.get("prompt_guard"))
        self.async_provider = (
            normalize_provider_kind(self.preset.get("provider_kind")) == PROVIDER_KIND_ASYNC_JSON
        )
        self.remote = self.unit.get("remote") if isinstance(self.unit.get("remote"), dict) else None
        self.remote_phase = str((self.remote or {}).get("phase") or "")
        self.diagnostics = UnitDiagnostics() if self.async_provider else None
        self.provider_kwargs: dict = {}
        if self.async_provider:
            self.provider_kwargs["provider_config"] = (
                (self.remote or {}).get("provider_config") or self.preset.get("provider_config") or {}
            )
        self.socks5_proxy = get_upstream_socks5_proxy()

    def stable_image_id(self, image_index: int) -> str:
        # Unit + result index is stable across recovery replays, so re-downloading
        # or re-saving a result upserts the same gallery row instead of duplicating it.
        return generate_stable_image_id(self.unit_id, image_index)

    async def parent_was_cancelled(self) -> bool:
        current = await run_db_operation(
            get_generate_job,
            self.parent_job_id,
            metric_name="check_generate_job_cancelled",
        )
        return bool(current and current.get("status") == "cancelled")

    async def write_remote_checkpoint(self, state: dict) -> None:
        """Persist the remote checkpoint under this claim; a lost fence aborts."""
        wrote = await run_db_operation(
            write_image_job_unit_remote,
            self.unit_id,
            claim_token=self.claim_token,
            remote=state,
            checkpointed=True,
            metric_name="write_image_job_unit_remote",
            critical=True,
        )
        if not wrote:
            self.session.mark_lease_lost()
            raise UnitLeaseLostError()

    async def should_cancel_remote(self) -> bool:
        """Only a user cancellation authorizes a best-effort remote cancel.

        A lease loss (or shutdown) must leave the remote task alone: a newer
        owner may already be polling it.
        """
        row = await run_db_operation(
            get_image_job_unit,
            self.unit_id,
            metric_name="classify_image_unit_lease_loss",
        )
        return bool(row and row.get("status") == "cancelled")

    def unit_diagnostics_payload(self) -> dict | None:
        if self.diagnostics is None or not self.diagnostics.has_records:
            return None
        self.diagnostics.set_recovery(
            attempts=int(self.unit.get("attempts") or 0),
            recovery_count=int(self.unit.get("recovery_count") or 0),
        )
        return self.diagnostics.payload()

    async def complete_unit_from_checkpoint(self, state: dict) -> None:
        """Write the terminal success state staged by a previous owner.

        The gallery results were already saved (with stable ids) before the
        checkpoint was written, so replaying this never duplicates rows.
        """
        completion = state.get("completion") or {}
        metrics.increment(f"image_jobs.{self.operation}.recovered_completion")
        metrics.increment(f"image_jobs.{self.operation}.succeeded")
        if (
            await run_db_operation(
                complete_image_job_unit,
                self.unit_id,
                claim_token=self.claim_token,
                result=completion.get("result") or {"images": []},
                stage_timings=completion.get("stage_timings") or {},
                duration=completion.get("duration"),
                completed_at=completion.get("completed_at") or utc_now(),
                usage=completion.get("usage"),
                cost=completion.get("cost"),
                metric_name="complete_recovered_image_job_unit",
                critical=True,
            )
            is None
        ):
            raise UnitLeaseLostError()

    async def interrupt_unknown_submit(self, state: dict | None) -> None:
        """Stop a unit whose upstream submit result can never be known locally.

        The plan is explicit that this must not resubmit: without an idempotency
        key or a recorded task id, a second submit could bill a second
        generation that the first response never proved was not already sent.
        """
        recorded = UnitDiagnostics()
        recorded.set_code("submit_unknown")
        recorded.set_recovery(
            attempts=int(self.unit.get("attempts") or 0),
            recovery_count=int(self.unit.get("recovery_count") or 0),
            phase=(state or {}).get("phase"),
            submitted_at=(state or {}).get("submitted_at"),
        )
        recorded.record_event(
            "submit",
            "A provider submit may have been sent but its result was not recorded",
        )
        metrics.increment("image_jobs.unit_submit_unknown")
        await run_db_operation(
            fail_image_job_unit,
            self.unit_id,
            claim_token=self.claim_token,
            status="interrupted",
            stage="interrupted",
            message=(
                "Provider submit result is unknown; start a new generation to retry"
            ),
            error=(
                "Provider submit result is unknown: the worker stopped before "
                "recording the remote task, so no automatic resubmission was attempted"
            ),
            stage_timings={},
            completed_at=utc_now(),
            diagnostics=recorded.payload(),
            metric_name="interrupt_unknown_submit_image_job_unit",
            critical=True,
        )

    async def recover(self):
        if self.remote is not None and self.remote_phase == "results_ready":
            # A previous owner saved the results but could not write the
            # terminal state; replay it instead of querying upstream again.
            await self.complete_unit_from_checkpoint(self.remote)
            return True
        if self.async_provider:
            if self.remote_phase == "submitting":
                has_idempotency_key = False
                try:
                    resolved_provider = resolve_provider_config(
                        self.provider_kwargs.get("provider_config")
                    )
                    has_idempotency_key = bool(
                        resolved_provider.submit.idempotency_header
                        and self.remote.get("idempotency_key")
                    )
                except Exception:
                    has_idempotency_key = False
                if not has_idempotency_key:
                    await self.interrupt_unknown_submit(self.remote)
                    return True
                self.provider_kwargs["async_remote"] = self.remote
            elif self.remote_phase == "submitted":
                self.provider_kwargs["async_remote"] = self.remote
            elif self.remote_phase == "" and int(self.unit.get("attempts") or 0) > 1:
                # Reclaimed running unit without a checkpoint (pre-upgrade or a
                # crash before the first checkpoint write): the upstream submit
                # result cannot be inferred, so it is never auto-resubmitted.
                await self.interrupt_unknown_submit(None)
                return True
        if self.async_provider:
            self.provider_kwargs.update(
                {
                    "async_checkpoint": self.write_remote_checkpoint,
                    "async_cancel_remote": self.should_cancel_remote,
                    "async_diagnostics": self.diagnostics,
                }
            )
        return False

    async def start(self):
        start_stage = "starting_edit" if self.operation == "edit" else "starting_generation"
        start_message = (
            "Starting image edit" if self.operation == "edit" else "Starting image generation"
        )
        start_persist_at = time.monotonic()
        if (
            await run_db_operation(
                update_image_job_unit_progress,
                self.unit_id,
                claim_token=self.claim_token,
                stage=start_stage,
                message=start_message,
                claim_expires_at=image_unit_lease_expires_at(),
                metric_name="start_image_job_unit",
            )
            is None
        ):
            raise UnitLeaseLostError()
        self.session.note_lease_extended(start_persist_at)
        await store_generate_job_async(
            self.parent_job_id,
            {
                "status": "running",
                "stage": start_stage,
                "message": start_message,
                "operation": self.operation,
                "started_at": self.parent.get("started_at") or utc_now(),
            },
        )
        if int(self.parent.get("n") or 1) > 1:
            await aggregate_parent_image_job(self.parent_job_id, force_publish=True)
        metrics.increment(f"image_jobs.{self.operation}.started")

    def configure_stream(self):
        self.stream_kwargs: dict = {}
        if getattr(self.req, "stream", False):
            preview_sequence = 0
            unit_index = int(self.unit.get("unit_index") or 0)

            def on_preview(partial_image_index: int, mime_type: str, image_bytes: bytes, call_index: int = 0) -> None:
                nonlocal preview_sequence
                preview_sequence += 1
                data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
                publish_generate_job_preview(
                    self.parent_job_id,
                    {
                        "job_id": self.parent_job_id,
                        "unit_index": unit_index,
                        "call_index": call_index,
                        "partial_image_index": partial_image_index,
                        "sequence": preview_sequence,
                        "mime_type": mime_type,
                        "data_url": data_url,
                    },
                )

            self.stream_kwargs = {
                "stream": True,
                "partial_images": getattr(self.req, "partial_images", 2),
                "preview": on_preview,
            }
            metrics.increment("image_job.streaming_requested")
        if self.prompt_guard:
            self.stream_kwargs["prompt_guard"] = True

    async def run_upstream(self) -> list:
        with (
            use_job_stage_timer(self.stage_timer),
            use_usage_sink(self.usage_sink),
            use_image_id_factory(self.stable_image_id),
        ):
            if self.operation == "edit":
                edit_sources = [
                    edit_source_from_payload(source)
                    for source in self.unit.get("edit_sources") or []
                ]
                image_sources = [
                    source for source in edit_sources if source.role == "image"
                ]
                mask_source = next(
                    (source for source in edit_sources if source.role == "mask"),
                    None,
                )
                if not image_sources:
                    raise proxy.UpstreamApiError(
                        "At least one edit source image is required"
                    )
                if self.provider_kwargs:
                    # Declaratively mapped provider: the edit_submit section
                    # carries reference images and the mask upstream.
                    return await proxy.call_image_provider_edit_api(
                        self.api_url,
                        self.api_key,
                        self.req,  # type: ignore[arg-type]
                        image_sources,
                        self.api_preset_name,
                        self.session.progress,
                        socks5_proxy=self.socks5_proxy,
                        persist_gallery_entry=add_to_gallery_async,
                        mask_source=mask_source,
                        mask_coverage=mask_source.coverage if mask_source else None,
                        prompt_guard=self.prompt_guard,
                        **self.provider_kwargs,
                    )
                return await proxy.call_image_edit_api(
                    self.api_url,
                    self.api_key,
                    self.req,  # type: ignore[arg-type]
                    image_sources,
                    self.api_preset_name,
                    self.session.progress,
                    socks5_proxy=self.socks5_proxy,
                    persist_gallery_entry=add_to_gallery_async,
                    mask_source=mask_source,
                    mask_coverage=mask_source.coverage if mask_source else None,
                    **self.stream_kwargs,
                )
            return await proxy.call_image_generation_api(
                self.api_url,
                self.api_key,
                self.api_path,
                self.req,  # type: ignore[arg-type]
                self.api_preset_name,
                self.session.progress,
                socks5_proxy=self.socks5_proxy,
                persist_gallery_entry=add_to_gallery_async,
                **self.stream_kwargs,
                **self.provider_kwargs,
            )

    async def call_upstream(self) -> list:
        entries = await self.session.run_upstream(self.run_upstream)
        if not entries:
            raise proxy.UpstreamApiError("No image data in upstream response")
        return entries

    async def complete(self, entries: list):
        await self.session.flush_progress_updates()
        self.session.raise_if_lease_lost()
        duration_seconds = time.monotonic() - self.started_at
        duration = f"{duration_seconds:.2f}s"
        completed_at = beijing_now()

        def update_entries():
            return [
                update_gallery_entry(
                    entry.id,
                    {
                        "duration": duration,
                        "completed_at": completed_at,
                        "n": self.parent.get("n") or self.req.n,
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
        stage_timings = self.stage_timer.snapshot()
        usage = image_cost.normalize_usage(self.usage_sink.raw_usage)
        cost = image_cost.estimate_image_cost(self.req.model, usage)
        metrics.increment(f"image_jobs.{self.operation}.succeeded")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
        if usage is None:
            metrics.increment("image_job.usage_missing")
        if not cost.get("complete"):
            metrics.increment("image_job.cost_unknown_rate")
        if await self.parent_was_cancelled():
            await run_db_operation(
                fail_image_job_unit,
                self.unit_id,
                claim_token=self.claim_token,
                status="cancelled",
                stage="cancelled",
                message="Generation job cancelled",
                error="Generation job cancelled",
                stage_timings=stage_timings,
                duration=duration,
                completed_at=utc_now(),
                usage=usage,
                cost=cost,
                diagnostics=self.unit_diagnostics_payload(),
                metric_name="cancel_completed_image_job_unit",
                critical=True,
            )
            return
        # Stage the finished results before the terminal write: if this worker
        # dies here, a recovery owner replays only the terminal write.
        await self.write_remote_checkpoint(
            {
                **(self.remote or {}),
                "phase": "results_ready",
                "completion": {
                    "result": {"images": result_images},
                    "stage_timings": stage_timings,
                    "duration": duration,
                    "completed_at": completed_at,
                    "usage": usage,
                    "cost": cost,
                },
            }
        )
        if (
            await run_db_operation(
                complete_image_job_unit,
                self.unit_id,
                claim_token=self.claim_token,
                result={"images": result_images},
                stage_timings=stage_timings,
                duration=duration,
                completed_at=completed_at,
                usage=usage,
                cost=cost,
                metric_name="complete_image_job_unit",
                critical=True,
            )
            is None
        ):
            raise UnitLeaseLostError()

    async def cancel(self):
        await self.session.abort_upstream()
        duration_seconds = time.monotonic() - self.started_at
        stage_timings = self.stage_timer.snapshot()
        usage = image_cost.normalize_usage(self.usage_sink.raw_usage)
        cost = image_cost.estimate_image_cost(self.req.model, usage) if usage else None
        await self.session.flush_progress_before_terminal(suppress_cancelled=True)
        if self.async_provider:
            # Do not terminalize a mapped provider unit here: a user cancel
            # already marked it cancelled, and a shutdown must leave the
            # checkpoint for a new owner to resume (or classify as unknown).
            current = await run_db_operation(
                get_image_job_unit,
                self.unit_id,
                metric_name="classify_cancelled_image_unit",
            )
            if current and current.get("status") == "cancelled":
                metrics.increment(f"image_jobs.{self.operation}.cancelled")
            else:
                metrics.increment("image_jobs.unit_recovery_pending")
            logger.warning(
                "Mapped provider unit stopped without a terminal write, recovery owns it: "
                "unit_id=%s parent_job_id=%s worker_id=%s",
                self.unit_id,
                self.parent_job_id,
                self.worker_id,
            )
            return
        metrics.increment(f"image_jobs.{self.operation}.cancelled")
        await run_db_operation(
            fail_image_job_unit,
            self.unit_id,
            claim_token=self.claim_token,
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
            critical=True,
        )

    async def fail(self, error: Exception):
        error_message = redact_sensitive_text(
            get_exception_message(error), secret_values=(self.api_key, self.socks5_proxy)
        )
        status = (
            "upstream_error" if isinstance(error, proxy.UpstreamApiError) else "error"
        )
        duration_seconds = time.monotonic() - self.started_at
        stage_timings = self.stage_timer.snapshot()
        usage = image_cost.normalize_usage(self.usage_sink.raw_usage)
        cost = image_cost.estimate_image_cost(self.req.model, usage) if usage else None
        cancelled = await self.parent_was_cancelled()
        if not cancelled:
            metrics.increment(f"image_jobs.{self.operation}.failed")
            metrics.observe_ms("image_job.duration", duration_seconds * 1000)
            metrics.observe_job_stage_timings(stage_timings)
            redacted_traceback = redact_sensitive_text(
                "".join(traceback.format_exception(error)),
                secret_values=(self.api_key, self.socks5_proxy),
            )
            logger.error(
                "Image unit failed: unit_id=%s parent_job_id=%s worker_id=%s error_type=%s\n%s",
                self.unit_id,
                self.parent_job_id,
                self.worker_id,
                error.__class__.__name__,
                redacted_traceback,
            )
        await self.session.flush_progress_before_terminal(suppress_cancelled=False)
        if self.session.lease_lost.is_set():
            logger.warning(
                "Image unit lease lost before terminal write, skipping: "
                "unit_id=%s parent_job_id=%s worker_id=%s",
                self.unit_id,
                self.parent_job_id,
                self.worker_id,
            )
            return
        await run_db_operation(
            fail_image_job_unit,
            self.unit_id,
            claim_token=self.claim_token,
            status="cancelled" if cancelled else status,
            stage=(
                "cancelled"
                if cancelled
                else "generation_failed" if self.operation == "generation" else "edit_failed"
            ),
            message="Generation job cancelled" if cancelled else error_message,
            error="Generation job cancelled" if cancelled else error_message,
            stage_timings=stage_timings,
            duration=f"{duration_seconds:.2f}s",
            completed_at=utc_now(),
            usage=usage,
            cost=cost,
            diagnostics=self.unit_diagnostics_payload(),
            metric_name="fail_image_job_unit",
            critical=True,
        )

    async def cleanup(self):
        await self.session.close()
        if self.session.lease_lost.is_set():
            # Ownership moved elsewhere, so this worker must not derive or write
            # a parent update. Still refresh the local cache from storage so a
            # cross-worker cancellation cannot leave a `running` ghost behind.
            row = await run_db_operation(
                get_generate_job,
                self.parent_job_id,
                metric_name="refresh_lost_lease_parent",
            )
            if row is not None:
                await publish_generate_job_row_async(row, dispatch_webhook=False)
        else:
            await aggregate_parent_image_job(self.parent_job_id, force_publish=True)

    async def execute(self):
        try:
            await self.prepare()
            if self.snapshot_origin_changed:
                raise proxy.UpstreamApiError(
                    "API preset address changed after this job was queued; start a new task with the intended preset"
                )
            if await self.parent_was_cancelled():
                raise asyncio.CancelledError()
            if await self.recover():
                return
            await self.start()
            self.configure_stream()
            entries = await self.call_upstream()
            await self.complete(entries)
        except asyncio.CancelledError:
            await self.cancel()
        except UnitLeaseLostError:
            logger.warning(
                "Image unit lease lost, aborting without terminal write: "
                "unit_id=%s parent_job_id=%s worker_id=%s",
                self.unit_id, self.parent_job_id, self.worker_id,
            )
        except Exception as error:
            await self.fail(error)
        finally:
            await self.cleanup()
