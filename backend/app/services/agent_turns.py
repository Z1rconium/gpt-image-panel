"""Agent turn runner.

One background task runs each turn: it streams the model, executes the image
tools through the normal job queue, and records everything as SQLite rows and
replayable events, so a page reload or a second worker can follow the turn.
"""

import asyncio
import copy
import json
import logging
import os
import time
from contextlib import aclosing
from typing import Any

from ..core import settings as config
from ..core.api_paths import normalize_api_path
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.errors import DomainError
from ..core.redaction import redact_sensitive_text
from ..integrations import agent_client
from ..integrations.agent_client import (
    AgentClientError,
    AgentTimeoutError,
    AgentToolsUnsupportedError,
    AssistantTextItem,
    Finish,
    TextDelta,
    ToolCallComplete,
    ToolCallItem,
    ToolCallStarted,
    ToolResultItem,
)
from ..repositories import agent as agent_repo
from ..runtime.blocking import run_db_operation
from ..runtime.state import state, utc_lease_expires_at
from ..schemas.generation import EditRequest, GenerateRequest
from . import (
    agent_context,
    agent_prompt,
    agent_refs,
    agent_tools,
    assistant_runtime,
    edit_sources,
    job_cancel,
    job_queue,
)
from .job_events import resolve_generate_job_view

logger = logging.getLogger(__name__)

TERMINAL_EVENT_TYPES = frozenset({"turn.completed", "turn.failed", "turn.cancelled"})
TEXT_FLUSH_CHARS = 256
TEXT_FLUSH_SECONDS = 0.15
PERSIST_INTERVAL_SECONDS = 1.0
CANCEL_POLL_SECONDS = 1.0
IMAGE_POLL_MIN_SECONDS = 0.5
IMAGE_POLL_MAX_SECONDS = 2.0
MAX_REFS_PER_IMAGE = 8
MAX_INVALID_TOOL_CALLS = 2
MAX_OUTPUT_TOKENS = 4096
HISTORY_MESSAGE_LIMIT = 400
TOOLS_UNSUPPORTED_MESSAGE = (
    "This endpoint or model does not support tool calling. "
    "Choose a model that supports function calling in Settings."
)


class TurnCancelled(Exception):
    """The user asked to stop this turn."""


class TurnFailed(Exception):
    """The turn cannot continue; the message is shown to the user."""


def describe_failure(error: BaseException) -> str:
    if isinstance(error, TurnFailed):
        return str(error)
    if isinstance(error, AgentToolsUnsupportedError):
        return TOOLS_UNSUPPORTED_MESSAGE
    if isinstance(error, AgentTimeoutError):
        return "The model request timed out."
    if isinstance(error, AgentClientError):
        return redact_sensitive_text(str(error)) or "The model request failed."
    if isinstance(error, DomainError):
        return redact_sensitive_text(str(error.detail or error)) or "The Agent turn failed."
    logger.error("Agent turn failed unexpectedly", exc_info=error)
    return "The Agent turn failed unexpectedly."


class _RoundResult:
    def __init__(self, text: str, calls: list[ToolCallComplete], finish: Finish | None):
        self.text = text
        self.calls = calls
        self.finish = finish


class _TurnRun:
    """State of one running turn: blocks, text coalescing, cancel polling."""

    def __init__(self, turn: dict[str, Any]):
        self.turn = turn
        self.turn_id: str = turn["id"]
        self.conversation_id: str = turn["conversation_id"]
        self.round_no: int = int(turn["round_no"])
        self.message_id: str = turn["assistant_message_id"]
        self.agent: assistant_runtime.AgentRuntime | None = None
        self.blocks: list[dict[str, Any]] = []
        self.rounds_used = 0
        self.images_created = 0
        self._invalid_streak = 0
        self._block_seq = 0
        self._text_block: dict[str, Any] | None = None
        self._pending_text = ""
        self._last_text_emit = time.monotonic()
        self._last_persist = 0.0
        self._cancelled = False
        self._batch_blocks: dict[str, dict[str, Any]] = {}
        self._wakeup = state.agent_turn_wakeups.setdefault(self.turn_id, asyncio.Event())
        # Concurrent image tasks write from several coroutines; the locks keep the
        # stored event order and the persisted snapshot in call order.
        self._emit_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()

    # ── events and persistence ─────────────────────────────────

    def _next_block_id(self, prefix: str) -> str:
        self._block_seq += 1
        return f"{prefix}{self._block_seq}"

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(data)
        async with self._emit_lock:
            await run_db_operation(
                agent_repo.append_turn_event,
                self.turn_id,
                event_type,
                snapshot,
                metric_name="agent_append_turn_event",
            )
        self._wakeup.set()

    async def upsert_block(self, block: dict[str, Any]) -> None:
        for index, existing in enumerate(self.blocks):
            if existing["id"] == block["id"]:
                self.blocks[index] = block
                break
        else:
            self.blocks.append(block)
        await self.emit("block.upsert", {"block": block})
        # Structural changes are rare, so persist each one: a reload mid-turn then
        # reads the same blocks the event stream has shown so far.
        await self.persist()

    async def add_text(self, text: str) -> None:
        if not text:
            return
        if self._text_block is None:
            self._text_block = {"id": self._next_block_id("t"), "type": "text", "text": ""}
            await self.upsert_block(self._text_block)
        self._text_block["text"] += text
        self._pending_text += text
        if (
            len(self._pending_text) >= TEXT_FLUSH_CHARS
            or time.monotonic() - self._last_text_emit >= TEXT_FLUSH_SECONDS
        ):
            await self.flush_text()

    async def flush_text(self) -> None:
        if not self._pending_text or self._text_block is None:
            return
        delta, self._pending_text = self._pending_text, ""
        self._last_text_emit = time.monotonic()
        await self.emit("block.text", {"block_id": self._text_block["id"], "delta": delta})
        if time.monotonic() - self._last_persist >= PERSIST_INTERVAL_SECONDS:
            await self.persist()

    async def persist(self) -> None:
        async with self._persist_lock:
            self._last_persist = time.monotonic()
            text = "\n\n".join(
                block["text"] for block in self.blocks if block["type"] == "text" and block["text"].strip()
            )
            await run_db_operation(
                agent_repo.update_message_content,
                self.message_id,
                text=text,
                blocks=copy.deepcopy(self.blocks),
                metric_name="agent_update_message",
            )

    async def add_error_block(self, message: str) -> None:
        await self.flush_text()
        await self.upsert_block({"id": self._next_block_id("e"), "type": "error", "message": message})

    async def check_cancel(self) -> None:
        """Cooperative check; the DB polling lives in `_cancel_watcher`."""
        if self._cancelled:
            raise TurnCancelled()

    # ── the loop ───────────────────────────────────────────────

    async def run_loop(self) -> None:
        assert self.agent is not None
        agent = self.agent
        items = await self._initial_items()
        tools = agent_prompt.build_tools()
        instructions = agent_prompt.build_instructions(
            max_rounds=agent.max_tool_rounds,
            user_preferences=agent.system_prompt,
        )
        while True:
            await self.check_cancel()
            tools_enabled = self.rounds_used < agent.max_tool_rounds
            self._text_block = None
            result = await self._model_round(items, tools, instructions, tools_enabled)
            if not result.calls:
                if not result.text.strip() and not any(
                    block["type"] in {"text", "image_task"} and (block.get("text") or block.get("ref_label"))
                    for block in self.blocks
                ):
                    raise TurnFailed("The model returned an empty response.")
                return
            if not tools_enabled:
                # The model ignored tool_choice=none; stop instead of looping.
                return
            self.rounds_used += 1
            await run_db_operation(
                agent_repo.set_turn_rounds_used,
                self.turn_id,
                self.rounds_used,
                metric_name="agent_rounds_used",
            )
            if result.text.strip():
                items.append(AssistantTextItem(result.text))
            created_rows: list[dict[str, Any]] = []
            outputs: list[tuple[ToolCallComplete, str]] = []
            for call in result.calls:
                output, rows = await self._execute_call(call)
                outputs.append((call, output))
                created_rows.extend(rows)
            for call, _output in outputs:
                items.append(ToolCallItem(call.call_id, call.name, call.arguments_json))
            for call, output in outputs:
                items.append(ToolResultItem(call.call_id, output))
            visuals = await agent_context.load_preview_data_urls(created_rows)
            result_item = agent_context.build_result_images_item(visuals)
            if result_item is not None:
                items.append(result_item)
            await self.persist()

    async def _initial_items(self) -> list[Any]:
        messages, image_refs, user_message = await asyncio.gather(
            run_db_operation(
                agent_repo.list_messages,
                self.conversation_id,
                limit=HISTORY_MESSAGE_LIMIT,
                metric_name="agent_history_messages",
            ),
            run_db_operation(
                agent_repo.list_conversation_images,
                self.conversation_id,
                metric_name="agent_history_images",
            ),
            run_db_operation(
                agent_repo.get_message,
                self.turn["user_message_id"],
                metric_name="agent_user_message",
            ),
        )
        history = agent_context.build_history_items(
            messages=messages,
            image_refs=image_refs,
            current_round=self.round_no,
        )
        user_text = str((user_message or {}).get("text") or "")
        visual_rows = agent_context.select_visual_context(
            current_text=user_text,
            current_round=self.round_no,
            image_refs=image_refs,
        )
        visuals = await agent_context.load_preview_data_urls(visual_rows)
        for index in range(len(history) - 1, -1, -1):
            if isinstance(history[index], agent_client.UserItem):
                history[index] = agent_context.build_current_user_item(
                    history_item=history[index],
                    visuals=visuals,
                )
                break
        return history

    async def _model_round(
        self,
        items: list[Any],
        tools: list[agent_client.ToolSpec],
        instructions: str,
        tools_enabled: bool,
    ) -> _RoundResult:
        assert self.agent is not None
        runtime = self.agent.assistant
        stripper = agent_refs.RefTagStripper()
        visible_parts: list[str] = []
        calls: list[ToolCallComplete] = []
        finish: Finish | None = None
        async with assistant_runtime.assistant_request_limit(runtime.timeout_seconds, wait_for_slot=True):
            stream = agent_client.stream_agent_response(
                api_url=runtime.api_url,
                api_key=runtime.api_key,
                api_path=runtime.api_path,
                model=runtime.model,
                instructions=instructions,
                items=items,
                tools=tools,
                tool_choice="auto" if tools_enabled else "none",
                timeout_seconds=runtime.timeout_seconds,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
            async with aclosing(stream):
                async for event in stream:
                    await self.check_cancel()
                    if isinstance(event, TextDelta):
                        visible = stripper.feed(event.text)
                        if visible:
                            visible_parts.append(visible)
                            await self.add_text(visible)
                    elif isinstance(event, ToolCallStarted):
                        await self.flush_text()
                        self._text_block = None
                        if event.name == agent_prompt.TOOL_GENERATE_IMAGE_BATCH:
                            block = {
                                "id": self._next_block_id("p"),
                                "type": "batch_params",
                                "call_id": event.call_id,
                                "status": "streaming",
                                "items": [],
                            }
                            self._batch_blocks[event.call_id] = block
                            await self.upsert_block(block)
                    elif isinstance(event, ToolCallComplete):
                        calls.append(event)
                    elif isinstance(event, Finish):
                        finish = event
        tail = stripper.flush()
        if tail:
            visible_parts.append(tail)
            await self.add_text(tail)
        await self.flush_text()
        return _RoundResult("".join(visible_parts), calls, finish)

    # ── tools ──────────────────────────────────────────────────

    async def _execute_call(self, call: ToolCallComplete) -> tuple[str, list[dict[str, Any]]]:
        block = self._batch_blocks.get(call.call_id)
        try:
            parsed = agent_tools.parse_tool_arguments(call.name, call.arguments_json)
        except agent_tools.ToolArgumentError as error:
            self._invalid_streak += 1
            if block is not None:
                block["status"] = "invalid"
                await self.upsert_block(block)
            if self._invalid_streak >= MAX_INVALID_TOOL_CALLS:
                raise TurnFailed("The model sent invalid tool arguments repeatedly.") from error
            return json.dumps({"error": str(error)}), []
        self._invalid_streak = 0
        if isinstance(parsed, agent_tools.ContinueRequest):
            return json.dumps({"ok": True}), []
        if block is None:
            block = {
                "id": self._next_block_id("p"),
                "type": "batch_params",
                "call_id": call.call_id,
                "status": "ready",
                "items": [],
            }
            self._batch_blocks[call.call_id] = block
        block["status"] = "ready"
        block["items"] = [{"id": image.id, "prompt": image.prompt} for image in parsed]
        await self.upsert_block(block)
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
        for image in images:
            labels = agent_refs.extract_ref_tags(image.prompt)
            if len(labels) > MAX_REFS_PER_IMAGE:
                results[image.id] = _item_error(image.id, f"At most {MAX_REFS_PER_IMAGE} references per image.")
                continue
            ref_rows: list[dict[str, Any]] = []
            problem = None
            for label in labels:
                row = await run_db_operation(
                    agent_repo.get_image_by_label,
                    self.conversation_id,
                    label,
                    metric_name="agent_get_image_ref",
                )
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
            row = await run_db_operation(
                agent_repo.insert_pending_output_image,
                conversation_id=self.conversation_id,
                turn_id=self.turn_id,
                message_id=self.message_id,
                round_no=self.round_no,
                item_id=image.id,
                prompt=image.prompt,
                mode=mode,
                metric_name="agent_insert_pending_image",
            )
            self.images_created += 1
            block = {
                "id": self._next_block_id("i"),
                "type": "image_task",
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
            await self.upsert_block(block)
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
        params = self.turn.get("image_params") or {}
        sources: list[job_queue.EditImageSource] = []
        try:
            common = {
                "prompt": self._send_prompt(image.prompt, [ref["ref_label"] for ref in ref_rows]),
                "size": params.get("size", "auto"),
                "quality": params.get("quality", "auto"),
                "output_format": params.get("output_format", "png"),
            }
            if ref_rows:
                request = EditRequest(**common)
                for ref in ref_rows:
                    sources.append(await edit_sources.read_gallery_edit_source(ref["image_id"]))
                job = await job_queue.queue_edit_job(req=request, image_sources=sources)
                sources = []
            else:
                job = await job_queue.queue_image_job(
                    req=GenerateRequest(**common),
                    operation="generation",
                    api_path=lambda preset: normalize_api_path(
                        str(preset.get("api_path") or "/v1/images/generations")
                    ),
                    queued_message="Queued image generation",
                )
        except asyncio.CancelledError:
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
        await run_db_operation(agent_repo.set_image_job, row["id"], job_id, metric_name="agent_set_image_job")
        block["job_id"] = job_id
        await self.upsert_block(block)

        deadline = time.monotonic() + config.AGENT_IMAGE_JOB_TIMEOUT_SECONDS
        delay = IMAGE_POLL_MIN_SECONDS
        last_stage = block["stage"]
        while True:
            await self.check_cancel()
            view = await resolve_generate_job_view(job_id)
            if view is None:
                return await self._fail_image(image, row, block, "The image job disappeared.")
            status = str(view.get("status") or "")
            stage = str(view.get("stage") or status or "queued")
            if stage != last_stage:
                last_stage = stage
                block["stage"] = stage
                await self.upsert_block(block)
            if status not in ACTIVE_GENERATE_JOB_STATUSES:
                break
            if time.monotonic() >= deadline:
                return await self._fail_image(
                    image,
                    row,
                    block,
                    "Timed out waiting for the image job; it may still finish in the gallery.",
                )
            await asyncio.sleep(delay)
            delay = min(IMAGE_POLL_MAX_SECONDS, delay * 1.5)

        images_out = view.get("images") or []
        first = images_out[0] if images_out and isinstance(images_out[0], dict) else {}
        image_id = first.get("image_id") or view.get("image_id")
        if status in {"success", "partial_failure"} and image_id:
            settled = await run_db_operation(
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
            await self.upsert_block(block)
            return {"id": image.id, "status": "created", "ref": row["ref_label"]}, settled
        if status == "cancelled":
            settled = await run_db_operation(
                agent_repo.settle_image, row["id"], status="cancelled", metric_name="agent_settle_image"
            )
            block.update(status="cancelled", stage="cancelled", error=None)
            await self.upsert_block(block)
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
        settled = await run_db_operation(
            agent_repo.settle_image,
            row["id"],
            status="failed",
            error=message,
            metric_name="agent_settle_image",
        )
        block.update(status="failed", stage="failed", error=message)
        await self.upsert_block(block)
        return {"id": image.id, "status": "failed", "error": message}, settled

    # ── finishing ──────────────────────────────────────────────

    async def _cancel_pending_images(self) -> None:
        pending = await run_db_operation(
            agent_repo.list_pending_images_for_turn,
            self.turn_id,
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
            for block in self.blocks:
                if block["type"] == "image_task" and block["ref_label"] == row["ref_label"]:
                    block.update(status="cancelled", stage="cancelled")
                    await self.upsert_block(block)

    async def finalize(self, status: str, message: str | None) -> None:
        try:
            await self.flush_text()
            if status == "cancelled":
                await self._cancel_pending_images()
            await self.persist()
        except Exception:
            logger.warning("Agent turn %s could not flush its final state", self.turn_id, exc_info=True)
        await run_db_operation(
            agent_repo.finish_turn,
            self.turn_id,
            status,
            error_message=message,
            rounds_used=self.rounds_used,
            metric_name="agent_finish_turn",
            critical=True,
        )
        if status == "completed":
            await self.emit("turn.completed", {"rounds_used": self.rounds_used})
        elif status == "cancelled":
            await self.emit("turn.cancelled", {})
        else:
            await self.emit("turn.failed", {"message": message or "The Agent turn failed."})


def _item_error(item_id: str, message: str) -> dict[str, Any]:
    return {"id": item_id, "status": "error", "error": message}


async def _renew_lease_loop(turn_id: str, owner: str) -> None:
    interval = max(1.0, min(10.0, config.AGENT_TURN_LEASE_SECONDS / 3))
    while True:
        await asyncio.sleep(interval)
        try:
            renewed = await run_db_operation(
                agent_repo.renew_turn_lease,
                turn_id,
                owner=owner,
                lease_expires_at=utc_lease_expires_at(config.AGENT_TURN_LEASE_SECONDS),
                metric_name="agent_renew_lease",
                critical=True,
            )
        except Exception:
            logger.warning("Agent turn %s lease renewal failed", turn_id, exc_info=True)
            continue
        if not renewed:
            return


async def _cancel_watcher(run: _TurnRun, main: asyncio.Task) -> None:
    """Cancel the main loop as soon as a cancel is requested, even mid model call."""
    while not main.done():
        await asyncio.sleep(CANCEL_POLL_SECONDS)
        try:
            requested = await run_db_operation(
                agent_repo.is_turn_cancel_requested,
                run.turn_id,
                metric_name="agent_cancel_check",
            )
        except Exception:
            logger.warning("Agent turn %s cancel check failed", run.turn_id, exc_info=True)
            continue
        if requested:
            run._cancelled = True
            main.cancel()
            return


async def run_turn(turn_id: str) -> None:
    owner = f"{state.worker_id}-{os.urandom(4).hex()}"
    claimed = await run_db_operation(
        agent_repo.claim_turn,
        turn_id,
        owner=owner,
        lease_expires_at=utc_lease_expires_at(config.AGENT_TURN_LEASE_SECONDS),
        metric_name="agent_claim_turn",
        critical=True,
    )
    if not claimed:
        return
    turn = await run_db_operation(agent_repo.get_turn, turn_id, metric_name="agent_get_turn")
    if turn is None:
        return
    run = _TurnRun(turn)
    renewer = asyncio.create_task(_renew_lease_loop(turn_id, owner), name=f"agent-lease-{turn_id}")
    status = "failed"
    message: str | None = None
    try:
        try:
            await run.emit(
                "turn.started",
                {"turn_id": turn_id, "round_no": run.round_no, "model": turn["model"]},
            )
            run.agent = await assistant_runtime.resolve_agent_runtime_async()
            main = asyncio.create_task(run.run_loop(), name=f"agent-loop-{turn_id}")
            watcher = asyncio.create_task(_cancel_watcher(run, main), name=f"agent-cancel-{turn_id}")
            try:
                await main
            finally:
                watcher.cancel()
            status = "completed"
        except TurnCancelled:
            status = "cancelled"
        except asyncio.CancelledError:
            if run._cancelled:
                status = "cancelled"
            else:
                await asyncio.shield(
                    run.finalize("interrupted", "The server stopped before the turn finished.")
                )
                raise
        except Exception as error:
            message = describe_failure(error)
        if status == "failed":
            try:
                await run.add_error_block(message or "The Agent turn failed.")
            except Exception:
                logger.warning("Agent turn %s could not record its error block", turn_id, exc_info=True)
        await run.finalize(status, message)
    finally:
        renewer.cancel()
        state.agent_turn_wakeups.pop(turn_id, None)


def spawn_turn(turn_id: str) -> asyncio.Task:
    tasks = state.agent_turn_tasks
    task = asyncio.create_task(run_turn(turn_id), name=f"agent-turn-{turn_id}")
    tasks[turn_id] = task
    task.add_done_callback(lambda _task: tasks.pop(turn_id, None))
    return task
