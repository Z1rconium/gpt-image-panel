"""Agent conversation use cases: CRUD, turn admission, cancel, snapshots."""

import asyncio
import logging
import time
from typing import Any

from ..core import settings as config
from ..core.errors import DomainError, NotFoundError, RateLimitedError, UnprocessableRequestError
from ..repositories import agent as agent_repo
from ..runtime.blocking import run_db_operation
from ..runtime.state import state, utc_lease_expires_at
from ..schemas.agent import (
    AgentActiveTurn,
    AgentBranchSelectRequest,
    AgentConversationCreateRequest,
    AgentConversationDetail,
    AgentConversationListResponse,
    AgentConversationRenameRequest,
    AgentConversationSummary,
    AgentImageRef,
    AgentMessage,
    AgentTurnAccepted,
    AgentTurnRequest,
    AgentTurnStatus,
)
from . import agent_turns, assistant_runtime
from .job_events import resolve_generate_job_view
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES

logger = logging.getLogger(__name__)

DEFAULT_DETAIL_LIMIT = 200
MAX_DETAIL_LIMIT = 400
EVENT_PURGE_INTERVAL_SECONDS = 60.0
DELETE_WAIT_SECONDS = 10.0
_IMAGE_BLOCK_FIELDS = ("status", "image_id", "error")


def _summary(row: dict[str, Any]) -> AgentConversationSummary:
    return AgentConversationSummary(**row)


async def list_conversations() -> AgentConversationListResponse:
    rows = await run_db_operation(agent_repo.list_conversations, metric_name="agent_list_conversations")
    return AgentConversationListResponse(items=[_summary(row) for row in rows])


async def create_conversation(req: AgentConversationCreateRequest) -> AgentConversationSummary:
    try:
        row = await run_db_operation(
            agent_repo.create_conversation, req.title, metric_name="agent_create_conversation"
        )
    except agent_repo.AgentConversationLimitError as error:
        raise DomainError(str(error), status_code=409) from error
    return _summary(row)


async def rename_conversation(conversation_id: str, req: AgentConversationRenameRequest) -> AgentConversationSummary:
    row = await run_db_operation(
        agent_repo.rename_conversation, conversation_id, req.title, metric_name="agent_rename_conversation"
    )
    if row is None:
        raise NotFoundError("Agent conversation not found")
    return _summary(row)


async def delete_conversation(conversation_id: str) -> None:
    conversation = await run_db_operation(
        agent_repo.get_conversation, conversation_id, metric_name="agent_get_conversation"
    )
    if conversation is None:
        raise NotFoundError("Agent conversation not found")
    active_turn_id = conversation.get("active_turn_id")
    if active_turn_id:
        await _request_cancel(active_turn_id)
        deadline = time.monotonic() + DELETE_WAIT_SECONDS
        while time.monotonic() < deadline:
            turn = await run_db_operation(agent_repo.get_turn, active_turn_id, metric_name="agent_get_turn")
            if turn is None or turn["status"] in agent_repo.TERMINAL_TURN_STATUSES:
                break
            await asyncio.sleep(0.25)
    await run_db_operation(
        agent_repo.delete_conversation, conversation_id, metric_name="agent_delete_conversation"
    )


async def _purge_events_if_due() -> None:
    now = time.monotonic()
    if now - float(getattr(state, "agent_last_event_purge_at", 0.0)) < EVENT_PURGE_INTERVAL_SECONDS:
        return
    state.agent_last_event_purge_at = now
    await run_db_operation(
        agent_repo.purge_turn_events, config.AGENT_EVENT_RETENTION_SECONDS, metric_name="agent_purge_events"
    )


async def start_turn(conversation_id: str, req: AgentTurnRequest) -> AgentTurnAccepted:
    # No await before reserving: all request coroutines on this worker observe
    # both running tasks and admissions still waiting for the database.
    if len(state.agent_turn_tasks) + state.agent_turn_reservations >= config.AGENT_MAX_ACTIVE_TURNS:
        turn = await run_db_operation(
            agent_repo.get_turn_by_client_id, conversation_id, req.client_turn_id,
            metric_name="agent_replay_turn",
        )
        if turn is not None:
            return _accepted(turn, created=False)
        raise RateLimitedError("Too many Agent turns are running. Try again shortly.")
    state.agent_turn_reservations += 1
    task = asyncio.create_task(_start_reserved_turn(conversation_id, req))
    admissions = state.agent_turn_admissions
    admissions.add(task)
    task.add_done_callback(admissions.discard)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # A database worker cannot be cancelled. Finish admission and launch the
        # runner even when the HTTP caller leaves, so no queued row is orphaned.
        try:
            await asyncio.shield(task)
        except Exception:
            logger.warning("Cancelled Agent admission failed", exc_info=True)
        raise


async def _start_reserved_turn(conversation_id: str, req: AgentTurnRequest) -> AgentTurnAccepted:
    try:
        return await _create_reserved_turn(conversation_id, req)
    finally:
        state.agent_turn_reservations -= 1


async def _create_reserved_turn(conversation_id: str, req: AgentTurnRequest) -> AgentTurnAccepted:
    replay = await run_db_operation(
        agent_repo.get_turn_by_client_id, conversation_id, req.client_turn_id,
        metric_name="agent_replay_turn",
    )
    if replay is not None:
        return _accepted(replay, created=False)
    agent = await assistant_runtime.resolve_agent_runtime_async()
    text = req.text.strip()
    if len(text) > config.AGENT_MAX_USER_TEXT_CHARS:
        raise UnprocessableRequestError(
            f"Message is too long. Max is {config.AGENT_MAX_USER_TEXT_CHARS} characters."
        )
    if len(req.attachments) > config.AGENT_MAX_ATTACHMENTS_PER_MESSAGE:
        raise UnprocessableRequestError(
            f"At most {config.AGENT_MAX_ATTACHMENTS_PER_MESSAGE} images can be attached to one message."
        )
    await run_db_operation(agent_repo.sweep_stale_turns, metric_name="agent_sweep_stale_turns")
    await _purge_events_if_due()
    try:
        result = await run_db_operation(
            agent_repo.create_turn,
            conversation_id,
            client_turn_id=req.client_turn_id,
            text=text,
            attachment_image_ids=[attachment.image_id for attachment in req.attachments],
            model=agent.assistant.model,
            image_params=req.image_params.model_dump(),
            lease_expires_at=utc_lease_expires_at(config.AGENT_TURN_LEASE_SECONDS),
            action=req.action,
            source_turn_id=req.source_turn_id,
            branch_revision=req.branch_revision,
            metric_name="agent_create_turn",
            critical=True,
        )
    except (agent_repo.AgentTurnConflictError, agent_repo.AgentConversationLimitError) as error:
        raise DomainError(str(error), status_code=409) from error
    except agent_repo.AgentAttachmentError as error:
        raise UnprocessableRequestError(str(error)) from error
    if result is None:
        raise NotFoundError("Agent conversation not found")
    turn, created = result
    if created:
        agent_turns.spawn_turn(turn["id"])
    return _accepted(turn, created)


def _accepted(turn: dict[str, Any], created: bool) -> AgentTurnAccepted:
    return AgentTurnAccepted(
        turn_id=turn["id"],
        conversation_id=turn["conversation_id"],
        round_no=turn["round_no"],
        status=turn["status"],
        user_message_id=turn["user_message_id"],
        assistant_message_id=turn["assistant_message_id"],
        replayed=not created,
    )


async def _request_cancel(turn_id: str) -> dict[str, Any] | None:
    """Set the durable cancel flag, then wake the runner if it lives in this process."""
    turn = await run_db_operation(agent_repo.request_turn_cancel, turn_id, metric_name="agent_cancel_turn")
    if turn is not None:
        agent_turns.wake_turn_cancel(turn_id)
    return turn


async def cancel_turn(turn_id: str) -> AgentTurnStatus:
    turn = await _request_cancel(turn_id)
    if turn is None:
        raise NotFoundError("Agent turn not found")
    return _turn_status(turn)


async def get_turn_status(turn_id: str) -> AgentTurnStatus:
    turn = await run_db_operation(agent_repo.get_turn, turn_id, metric_name="agent_get_turn")
    if turn is None:
        raise NotFoundError("Agent turn not found")
    return _turn_status(turn)


def _turn_status(turn: dict[str, Any]) -> AgentTurnStatus:
    return AgentTurnStatus(
        turn_id=turn["id"],
        conversation_id=turn["conversation_id"],
        round_no=turn["round_no"],
        status=turn["status"],
        rounds_used=turn["rounds_used"],
        error_message=turn["error_message"],
    )


async def _reconcile_images(conversation_id: str) -> None:
    """Settle image rows left pending by an interrupted turn from the real job state."""
    turns = await run_db_operation(
        agent_repo.list_turns_needing_reconcile, conversation_id, metric_name="agent_reconcile_turns"
    )
    for turn in turns:
        pending = await run_db_operation(
            agent_repo.list_pending_images_for_turn, turn["id"], metric_name="agent_pending_images"
        )
        for row in pending:
            if not row.get("job_id"):
                await run_db_operation(
                    agent_repo.settle_image,
                    row["id"],
                    status="failed",
                    error="The turn was interrupted before this image was queued.",
                    metric_name="agent_settle_image",
                )
                continue
            view = await resolve_generate_job_view(row["job_id"])
            if view is None:
                await run_db_operation(
                    agent_repo.settle_image,
                    row["id"],
                    status="failed",
                    error="The image job is no longer available.",
                    metric_name="agent_settle_image",
                )
                continue
            status = str(view.get("status") or "")
            if status in ACTIVE_GENERATE_JOB_STATUSES:
                continue
            images = view.get("images") or []
            first = images[0] if images and isinstance(images[0], dict) else {}
            image_id = first.get("image_id") or view.get("image_id")
            if status in {"success", "partial_failure"} and image_id:
                await run_db_operation(
                    agent_repo.settle_image, row["id"], status="succeeded", image_id=str(image_id),
                    metric_name="agent_settle_image",
                )
            elif status == "cancelled":
                await run_db_operation(
                    agent_repo.settle_image, row["id"], status="cancelled", metric_name="agent_settle_image"
                )
            else:
                await run_db_operation(
                    agent_repo.settle_image,
                    row["id"],
                    status="failed",
                    error=str(view.get("error") or view.get("message") or f"The image job ended with status {status}."),
                    metric_name="agent_settle_image",
                )


def _image_ref(row: dict[str, Any]) -> AgentImageRef:
    return AgentImageRef(
        ref_label=row["ref_label"],
        round_no=row["round_no"],
        image_index=row["image_index"],
        role=row["role"],
        image_id=row["image_id"],
        filename=row["filename"],
        job_id=row["job_id"],
        item_id=row["item_id"],
        prompt=row["prompt"],
        mode=row["mode"],
        status=row["status"],
        error=row["error"],
        deleted=row["status"] == "succeeded" and not row["image_id"],
        message_id=row["message_id"],
        image_ref_id=row["id"],
        turn_id=row["turn_id"],
        path_round_no=row.get("path_round_no"),
    )


def _overlay_blocks(blocks: list[dict[str, Any]], images: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Image identity and deletion are authoritative in agent_message_images, not in stored JSON."""
    overlaid: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") == "image_task" and block.get("ref_label") in images:
            row = images[block["ref_label"]]
            block = {
                **block,
                "status": row["status"],
                "image_id": row["image_id"],
                "filename": row["filename"],
                "error": row["error"],
                "job_id": row["job_id"] or block.get("job_id"),
                "deleted": row["status"] == "succeeded" and not row["image_id"],
                "path_round_no": row.get("path_round_no"),
            }
        overlaid.append(block)
    return overlaid


async def get_conversation_detail(
    conversation_id: str,
    *,
    before_seq: int | None = None,
    limit: int = DEFAULT_DETAIL_LIMIT,
) -> AgentConversationDetail:
    limit = max(1, min(int(limit), MAX_DETAIL_LIMIT))
    await run_db_operation(agent_repo.sweep_stale_turns, metric_name="agent_sweep_stale_turns")
    conversation = await run_db_operation(
        agent_repo.get_conversation, conversation_id, metric_name="agent_get_conversation"
    )
    if conversation is None:
        raise NotFoundError("Agent conversation not found")
    await _reconcile_images(conversation_id)
    conversation = await run_db_operation(
        agent_repo.get_conversation, conversation_id, metric_name="agent_get_conversation"
    ) or conversation
    snapshot = await run_db_operation(
        agent_repo.branch_snapshot,
        conversation_id,
        before_seq=before_seq,
        limit=limit + 1,
        metric_name="agent_branch_snapshot",
    )
    if snapshot is None:
        raise NotFoundError("Agent conversation not found")
    conversation = snapshot["conversation"]
    messages = snapshot["messages"]
    has_more = len(messages) > limit
    if has_more:
        messages = messages[1:]
    # Image refs are only needed for the messages in this page, so the response
    # stays bounded by the message limit instead of the whole conversation.
    message_ids = {message["id"] for message in messages}
    image_rows = [row for row in snapshot["images"] if row["message_id"] in message_ids]
    by_label = {row["ref_label"]: row for row in image_rows}
    active_turn = None
    if conversation.get("active_turn_id"):
        turn = await run_db_operation(
            agent_repo.get_turn, conversation["active_turn_id"], metric_name="agent_get_turn"
        )
        if turn is not None:
            active_turn = AgentActiveTurn(id=turn["id"], status=turn["status"], round_no=turn["round_no"])
    return AgentConversationDetail(
        conversation=_summary(conversation),
        messages=[
            AgentMessage(
                id=message["id"],
                turn_id=message["turn_id"],
                seq=message["seq"],
                round_no=message["round_no"],
                role=message["role"],
                text=message["text"],
                blocks=_overlay_blocks(message["blocks"], by_label),
                status=message["status"],
                created_at=message["created_at"],
                updated_at=message["updated_at"],
                path_round_no=message.get("path_round_no"),
            )
            for message in messages
        ],
        image_refs=[_image_ref(row) for row in image_rows],
        active_turn=active_turn,
        has_more=has_more,
        branches=snapshot["branches"],
    )


async def select_branch(conversation_id: str, req: AgentBranchSelectRequest) -> AgentConversationDetail:
    try:
        found = await run_db_operation(agent_repo.select_branch, conversation_id, req.selected_turn_id, req.expected_revision, metric_name="agent_select_branch", critical=True)
    except agent_repo.AgentTurnConflictError as error:
        raise DomainError(str(error), status_code=409) from error
    if not found:
        raise NotFoundError("Agent conversation not found")
    return await get_conversation_detail(conversation_id)
