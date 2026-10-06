"""Tool authorization, image batch execution and image-job lifecycle of one Agent turn.

``AgentToolExecutor`` owns the tool-side state (images created, invalid-call
streak, batch parameter blocks, queued job ids). It talks to its ``AgentRunContext``
only through block/event writes, the DB helper and the cancel check.
"""

import asyncio
import json
import logging
import time
from typing import Any

from ..core import settings as config
from ..core.api_paths import normalize_api_path
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.errors import DomainError, UnprocessableRequestError
from ..core.redaction import redact_sensitive_text
from ..integrations.agent_client import ToolCallComplete
from ..repositories import agent as agent_repo
from ..runtime.blocking import run_db_operation
from ..schemas.generation import EditRequest, GenerateRequest
from . import agent_prompt, agent_refs, agent_tools, edit_sources, job_cancel, job_queue
from .agent_turn_errors import TurnCancelled, TurnFailed
from .job_events import resolve_generate_job_view

logger = logging.getLogger(__name__)

IMAGE_POLL_MIN_SECONDS = 0.5
IMAGE_POLL_MAX_SECONDS = 2.0
MAX_REFS_PER_IMAGE = 8
MAX_INVALID_TOOL_CALLS = 2


def _item_error(item_id: str, message: str) -> dict[str, Any]:
    return {"id": item_id, "status": "error", "error": message}


class AgentToolExecutor:
    def __init__(self, run: Any) -> None:
        self.run = run
        self.images_created = 0
        self.invalid_streak = 0
        self.batch_blocks: dict[str, dict[str, Any]] = {}
        self.queued_jobs: set[str] = set()

    async def begin_call(self, event: Any) -> None:
        """A streamed tool call started: show its (still empty) batch parameters."""
        if event.name != agent_prompt.TOOL_GENERATE_IMAGE_BATCH:
            return
        block = {
            "id": self.run.events.next_block_id("p"),
            "type": "batch_params",
            "call_id": event.call_id,
            "status": "streaming",
            "items": [],
        }
        self.batch_blocks[event.call_id] = block
        await self.run.events.upsert_block(block)

    async def queue_owned(self, awaitable):
        task = asyncio.create_task(awaitable)
        try:
            job = await asyncio.shield(task)
            self.queued_jobs.add(job.job_id)
            await self.run.check_cancel()
            return job
        except (asyncio.CancelledError, TurnCancelled):
            try:
                job = await asyncio.shield(task)
                await job_cancel.cancel_image_job(job.job_id)
            except Exception:
                logger.warning("Could not clean up a cancelled Agent queue operation", exc_info=True)
            raise

    @property
    def image_tools_allowed(self) -> bool:
        """The persisted per-turn capability; legacy turns without a snapshot keep the old behavior."""
        capabilities = (self.run.turn.get("execution_snapshot") or {}).get("capabilities")
        return bool(capabilities.get("image_tools", True)) if isinstance(capabilities, dict) else True

    async def execute_call(self, call: ToolCallComplete) -> tuple[str, list[dict[str, Any]]]:
        if not self.image_tools_allowed:
            # Enforced at the execution point: model or web text cannot widen the turn's capabilities.
            return json.dumps({"error": "Image tools are not enabled for this turn."}), []
        block = self.batch_blocks.get(call.call_id)
        try:
            parsed = agent_tools.parse_tool_arguments(call.name, call.arguments_json)
        except agent_tools.ToolArgumentError as error:
            self.invalid_streak += 1
            if block is not None:
                block["status"] = "invalid"
                await self.run.events.upsert_block(block)
            if self.invalid_streak >= MAX_INVALID_TOOL_CALLS:
                raise TurnFailed("The model sent invalid tool arguments repeatedly.") from error
            return json.dumps({"error": str(error)}), []
        self.invalid_streak = 0
        if isinstance(parsed, agent_tools.ContinueRequest):
            return json.dumps({"ok": True}), []
        if block is None:
            block = {
                "id": self.run.events.next_block_id("p"),
                "type": "batch_params",
                "call_id": call.call_id,
                "status": "ready",
                "items": [],
            }
            self.batch_blocks[call.call_id] = block
        block["status"] = "ready"
        block["items"] = [{"id": image.id, "prompt": image.prompt} for image in parsed]
        await self.run.events.upsert_block(block)
        return await self._run_batch(call.call_id, parsed)

    async def _run_batch(
        self,
        call_id: str,
        images: list[agent_tools.BatchImage],
    ) -> tuple[str, list[dict[str, Any]]]:
        remaining = config.AGENT_MAX_IMAGES_PER_TURN - self.images_created
        if len(images) > remaining:
            message = (
                f"Only {max(0, remaining)} more image(s) can be created in this turn. "
                "Explain this to the user instead of generating more."
            )
            return json.dumps({"error": message}), []

        results: dict[str, dict[str, Any]] = {}
        plans: list[tuple[agent_tools.BatchImage, dict[str, Any], list[dict[str, Any]], dict[str, Any]]] = []
        path_ids: set[str] | None = None
        for image in images:
            await self.run.check_cancel()
            labels = agent_refs.extract_ref_tags(image.prompt)
            if len(labels) > MAX_REFS_PER_IMAGE:
                results[image.id] = _item_error(image.id, f"At most {MAX_REFS_PER_IMAGE} references per image.")
                continue
            ref_rows: list[dict[str, Any]] = []
            problem = None
            for label in labels:
                row = await self.run._db(
                    agent_repo.get_image_by_label,
                    self.run.conversation_id,
                    label,
                    metric_name="agent_get_image_ref",
                )
                if path_ids is None:
                    # Parent links are immutable after admission. Resolve this
                    # turn's path once for the batch, independently of the UI selection.
                    path_ids = set(await self.run._db(
                        agent_repo.path_turn_ids, self.run.conversation_id, self.run.turn_id,
                        metric_name="agent_tool_path",
                    ))
                if row is not None and row["turn_id"] not in path_ids:
                    row = None
                if row is None or row["status"] != "succeeded" or not row.get("image_id"):
                    problem = (
                        f"Reference {label} is not available: it is unknown, deleted, or not created yet. "
                        "If it depends on an image from this same call, generate that image first, "
                        "then call continue_generation."
                    )
                    break
                ref_rows.append(row)
            if problem:
                results[image.id] = _item_error(image.id, problem)
                continue
            mode = "edit" if ref_rows else "generate"
            row = await self.run._db(
                agent_repo.insert_pending_output_image,
                conversation_id=self.run.conversation_id,
                turn_id=self.run.turn_id,
                message_id=self.run.message_id,
                round_no=self.run.round_no,
                item_id=image.id,
                prompt=image.prompt,
                mode=mode,
                metric_name="agent_insert_pending_image",
            )
            self.images_created += 1
            block = {
                "id": self.run.events.next_block_id("i"),
                "type": "image_task",
                "path_round_no": self.run.path_round_no,
                "call_id": call_id,
                "item_id": image.id,
                "ref_label": row["ref_label"],
                "round_no": row["round_no"],
                "image_index": row["image_index"],
                "job_id": None,
                "prompt": image.prompt,
                "mode": mode,
                "source_refs": [ref["ref_label"] for ref in ref_rows],
                "status": "pending",
                "stage": "queued",
                "error": None,
                "image_id": None,
                "filename": None,
            }
            await self.run.events.upsert_block(block)
            plans.append((image, row, ref_rows, block))

        outcomes = await asyncio.gather(
            *(self._run_image(*plan) for plan in plans),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
        if any(isinstance(outcome, TurnCancelled) for outcome in outcomes):
            raise TurnCancelled()
        succeeded: list[dict[str, Any]] = []
        for (image, _row, _refs, _block), outcome in zip(plans, outcomes):
            if isinstance(outcome, BaseException):
                logger.error("Agent image task crashed", exc_info=outcome)
                results[image.id] = _item_error(image.id, "The image task failed unexpectedly.")
                continue
            result, settled = outcome
            results[image.id] = result
            if settled is not None and settled["status"] == "succeeded":
                succeeded.append(settled)
        ordered = [results[image.id] for image in images]
        return json.dumps(ordered, ensure_ascii=False), succeeded

    @staticmethod
    def _send_prompt(prompt: str, labels: list[str]) -> str:
        positions = {label: index for index, label in enumerate(labels, start=1)}

        def replace(match) -> str:
            return f"[image {positions.get(match.group(1), 1)}]"

        return agent_refs.strip_ref_tags(agent_refs.REF_TAG_RE.sub(replace, prompt)).strip()

    async def _run_image(
        self,
        image: agent_tools.BatchImage,
        row: dict[str, Any],
        ref_rows: list[dict[str, Any]],
        block: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        params = self.run.turn.get("image_params") or {}
        sources: list[job_queue.EditImageSource] = []
        try:
            await self.run.check_cancel()
            common = {
                "prompt": self._send_prompt(image.prompt, [ref["ref_label"] for ref in ref_rows]),
                "size": params.get("size", "auto"),
                "quality": params.get("quality", "auto"),
                "output_format": params.get("output_format", "png"),
            }
            image_binding = (self.run.turn.get("execution_snapshot") or {}).get("image") or {}
            pinned_preset = image_binding.get("preset_id")
            preset_snapshot = image_binding.get("preset")
            if pinned_preset:
                if not isinstance(preset_snapshot, dict):
                    raise UnprocessableRequestError(
                        "This older turn has no image preset configuration snapshot. Start a new turn to create images."
                    )
                common["api_preset_id"] = str(pinned_preset)
            if ref_rows:
                request = EditRequest(**common)
                for ref in ref_rows:
                    sources.append(await edit_sources.read_gallery_edit_source(ref["image_id"]))
                await self.run.check_cancel()
                job = await self.queue_owned(job_queue.queue_edit_job(
                    req=request, image_sources=sources,
                    agent_turn_id=self.run.turn_id, agent_conversation_id=self.run.conversation_id,
                    preset_snapshot=preset_snapshot,
                ))
                sources = []
            else:
                await self.run.check_cancel()
                job = await self.queue_owned(job_queue.queue_image_job(
                    req=GenerateRequest(**common),
                    operation="generation",
                    api_path=lambda preset: normalize_api_path(
                        str(preset.get("api_path") or "/v1/images/generations")
                    ),
                    queued_message="Queued image generation",
                    agent_turn_id=self.run.turn_id,
                    agent_conversation_id=self.run.conversation_id,
                    preset_snapshot=preset_snapshot,
                ))
        except (asyncio.CancelledError, TurnCancelled):
            edit_sources.cleanup_edit_sources(sources)
            raise
        except DomainError as error:
            edit_sources.cleanup_edit_sources(sources)
            return await self._fail_image(image, row, block, redact_sensitive_text(str(error.detail or error)))
        except ValueError as error:
            edit_sources.cleanup_edit_sources(sources)
            return await self._fail_image(image, row, block, redact_sensitive_text(str(error)))
        except Exception:
            edit_sources.cleanup_edit_sources(sources)
            logger.exception("Agent could not queue an image job")
            return await self._fail_image(image, row, block, "The image job could not be queued.")

        job_id = job.job_id
        await self.run._db(agent_repo.set_image_job, row["id"], job_id, metric_name="agent_set_image_job")
        block["job_id"] = job_id
        await self.run.events.upsert_block(block)

        deadline = time.monotonic() + config.AGENT_IMAGE_JOB_TIMEOUT_SECONDS
        delay = IMAGE_POLL_MIN_SECONDS
        last_stage = block["stage"]
        while True:
            await self.run.check_cancel()
            view = await resolve_generate_job_view(job_id)
            if view is None:
                return await self._fail_image(image, row, block, "The image job disappeared.")
            status = str(view.get("status") or "")
            stage = str(view.get("stage") or status or "queued")
            if stage != last_stage:
                last_stage = stage
                block["stage"] = stage
                await self.run.events.upsert_block(block)
            if status not in ACTIVE_GENERATE_JOB_STATUSES:
                break
            if time.monotonic() >= deadline:
                # The wait window ended, not the job: keep the row pending so
                # reconciliation settles it from the real job state later.
                block.update(status="pending", error=None)
                await self.run.events.upsert_block(block)
                return {
                    "id": image.id,
                    "status": "still_running",
                    "note": "Waiting ended; the image job is still running and will appear in the gallery.",
                }, None
            await asyncio.sleep(delay)
            delay = min(IMAGE_POLL_MAX_SECONDS, delay * 1.5)

        images_out = view.get("images") or []
        first = images_out[0] if images_out and isinstance(images_out[0], dict) else {}
        image_id = first.get("image_id") or view.get("image_id")
        if status in {"success", "partial_failure"} and image_id:
            settled = await self.run._db(
                agent_repo.settle_image,
                row["id"],
                status="succeeded",
                image_id=str(image_id),
                metric_name="agent_settle_image",
            )
            block.update(
                status="succeeded",
                stage="completed",
                error=None,
                image_id=(settled or {}).get("image_id"),
                filename=(settled or {}).get("filename"),
            )
            await self.run.events.upsert_block(block)
            return {"id": image.id, "status": "created", "ref": row["ref_label"]}, settled
        if status == "cancelled":
            settled = await self.run._db(
                agent_repo.settle_image, row["id"], status="cancelled", metric_name="agent_settle_image"
            )
            block.update(status="cancelled", stage="cancelled", error=None)
            await self.run.events.upsert_block(block)
            return {"id": image.id, "status": "cancelled"}, settled
        message = redact_sensitive_text(
            view.get("error") or view.get("message") or f"The image job ended with status {status}."
        )
        return await self._fail_image(image, row, block, message)

    async def _fail_image(
        self,
        image: agent_tools.BatchImage,
        row: dict[str, Any],
        block: dict[str, Any],
        message: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        settled = await self.run._db(
            agent_repo.settle_image,
            row["id"],
            status="failed",
            error=message,
            metric_name="agent_settle_image",
        )
        block.update(status="failed", stage="failed", error=message)
        await self.run.events.upsert_block(block)
        return {"id": image.id, "status": "failed", "error": message}, settled

    async def cancel_pending_images(self) -> None:
        for job_id in self.queued_jobs:
            try:
                view = await resolve_generate_job_view(job_id)
                if view and view.get("status") in ACTIVE_GENERATE_JOB_STATUSES:
                    await job_cancel.cancel_image_job(job_id)
            except Exception:
                logger.warning("Failed to clean up Agent image job %s", job_id, exc_info=True)
        pending = await self.run._db(
            agent_repo.list_pending_images_for_turn,
            self.run.turn_id,
            metric_name="agent_pending_images",
        )
        for row in pending:
            if row.get("job_id"):
                try:
                    await job_cancel.cancel_image_job(row["job_id"])
                except DomainError:
                    pass
                except Exception:
                    logger.warning("Failed to cancel agent image job %s", row["job_id"], exc_info=True)
            await run_db_operation(
                agent_repo.settle_image, row["id"], status="cancelled", metric_name="agent_settle_image"
            )
            for block in self.run.events.blocks:
                if block["type"] == "image_task" and block["ref_label"] == row["ref_label"]:
                    block.update(status="cancelled", stage="cancelled")
                    await self.run.events.upsert_block(block)
