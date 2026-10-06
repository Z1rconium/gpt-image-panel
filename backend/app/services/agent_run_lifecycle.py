"""Claim, lease, cancellation and finalization of one Agent run."""

import asyncio
import logging
import os
import time
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from ..core import settings as config
from ..repositories import agent as agent_repo
from ..runtime.blocking import run_db_operation
from ..runtime.state import state, utc_lease_expires_at
from . import assistant_runtime
from .agent_turn_errors import TurnCancelled, describe_failure

if TYPE_CHECKING:
    from .agent_run_context import AgentRunContext

logger = logging.getLogger(__name__)
EVENT_FLUSH_INTERVAL_SECONDS = 0.25
CANCEL_POLL_SECONDS = 1.0


async def _stop_task(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


async def _event_flush_loop(run: "AgentRunContext") -> None:
    """Bound how long buffered events or block snapshots can sit unwritten."""
    while True:
        await asyncio.sleep(EVENT_FLUSH_INTERVAL_SECONDS)
        try:
            await run.events.flush_events()
            await run.events.maybe_persist()
        except Exception:
            logger.warning("Agent turn %s could not flush events", run.turn_id, exc_info=True)


async def _renew_lease_loop(run: "AgentRunContext", owner: str, main: asyncio.Task) -> None:
    turn_id = run.turn_id
    interval = max(1.0, min(10.0, config.AGENT_TURN_LEASE_SECONDS / 3))
    remaining = config.AGENT_TURN_LEASE_SECONDS
    if run.turn.get("lease_expires_at"):
        remaining = (
            datetime.fromisoformat(run.turn["lease_expires_at"]) - datetime.now(timezone.utc)
        ).total_seconds()
    expires = time.monotonic() + max(0, remaining)
    while True:
        await asyncio.sleep(min(interval, max(0, expires - time.monotonic())))
        try:
            started = time.monotonic()
            renewed = await asyncio.wait_for(run._db(
                agent_repo.renew_turn_lease,
                turn_id,
                owner=owner,
                lease_expires_at=utc_lease_expires_at(config.AGENT_TURN_LEASE_SECONDS),
                metric_name="agent_renew_lease",
                critical=True,
            ), timeout=max(0, expires - time.monotonic()))
        except Exception:
            logger.warning("Agent turn %s lease renewal failed", turn_id, exc_info=True)
            if time.monotonic() < expires:
                continue
            renewed = False
        if not renewed:
            run._lease_lost = True
            main.cancel()
            return
        expires = started + config.AGENT_TURN_LEASE_SECONDS


async def _cancel_watcher(run: "AgentRunContext", main: asyncio.Task) -> None:
    """Cancel the main loop as soon as a cancel is requested, even mid model call."""
    wake = state.agent_turn_cancel_events.setdefault(run.turn_id, asyncio.Event())
    while not main.done():
        # Clear before checking: a cancel landing after the clear leaves the event
        # set, and one landing before it is already visible in the DB check.
        wake.clear()
        try:
            requested = await run._db(
                agent_repo.is_turn_cancel_requested,
                run.turn_id,
                metric_name="agent_cancel_check",
            )
        except Exception:
            logger.warning("Agent turn %s cancel check failed", run.turn_id, exc_info=True)
            requested = False
        if requested:
            run._cancelled = True
            main.cancel()
            return
        try:
            await asyncio.wait_for(wake.wait(), timeout=CANCEL_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


class AgentRunLifecycle:
    def __init__(self, run: "AgentRunContext") -> None:
        self.run = run

    async def finalize(self, status: str, message: str | None) -> None:
        self.run._finishing = True
        if self.run._lease_lost:
            status, message = "interrupted", "The Agent turn lost its execution lease."
        if status != "completed":
            try:
                await self.run.tools.cancel_pending_images()
            except Exception:
                logger.warning("Agent turn %s could not clean up pending images", self.run.turn_id, exc_info=True)
        try:
            await self.run.search.settle(self.run, status)
            await self.run.search.publish_sources(self.run)
            await self.run.events.flush_text()
            # Everything is persisted before the turn status flips: a stream
            # reader that sees the terminal status synthesizes its own terminal
            # event and would never replay a later block event.
            await self.run.events.flush_events()
            await self.run.events.persist()
        except asyncio.CancelledError:
            if not self.run._lease_lost:
                raise
            status, message = "interrupted", "The Agent turn lost its execution lease."
            await self.run.tools.cancel_pending_images()
        except Exception:
            logger.warning("Agent turn %s could not flush its final state", self.run.turn_id, exc_info=True)
        settled = await self.run._db(
            agent_repo.finish_turn,
            self.run.turn_id,
            status,
            error_message=message,
            rounds_used=self.run.rounds_used,
            owner=self.run.turn.get("lease_owner"),
            metric_name="agent_finish_turn",
            critical=True,
        )
        if settled is None or settled["status"] != status or self.run._lease_lost:
            return
        self.run._finalized = True
        if status == "completed":
            await self.run.events.emit("turn.completed", {"rounds_used": self.run.rounds_used})
        elif status == "cancelled":
            await self.run.events.emit("turn.cancelled", {})
        else:
            await self.run.events.emit("turn.failed", {"message": message or "The Agent turn failed."})
        try:
            await self.run.events.flush_events()
        except Exception:
            logger.warning("Agent turn %s could not write its terminal event", self.run.turn_id, exc_info=True)

    @staticmethod
    async def execute(turn_id: str, context_factory: Callable[[dict], "AgentRunContext"]) -> None:
        owner = f"{state.worker_id}-{os.urandom(4).hex()}"
        claim = asyncio.create_task(run_db_operation(
            agent_repo.claim_turn,
            turn_id,
            owner=owner,
            lease_expires_at=utc_lease_expires_at(config.AGENT_TURN_LEASE_SECONDS),
            metric_name="agent_claim_turn",
            critical=True,
        ))
        try:
            claimed = await asyncio.shield(claim)
        except asyncio.CancelledError:
            if await asyncio.shield(claim):
                await asyncio.shield(run_db_operation(
                    agent_repo.finish_turn, turn_id, "interrupted", owner=owner,
                    error_message="The server stopped before the turn started.", critical=True,
                ))
            raise
        if not claimed:
            return
        try:
            turn = await run_db_operation(agent_repo.get_turn, turn_id, metric_name="agent_get_turn")
        except BaseException:
            await asyncio.shield(run_db_operation(
                agent_repo.finish_turn, turn_id, "interrupted", owner=owner,
                error_message="The turn could not start.", critical=True,
            ))
            raise
        if turn is None:
            return
        run = context_factory(turn)
        run._main_task = asyncio.current_task()
        renewer = asyncio.create_task(_renew_lease_loop(run, owner, asyncio.current_task()), name=f"agent-lease-{turn_id}")
        flusher = asyncio.create_task(_event_flush_loop(run), name=f"agent-flush-{turn_id}")
        status = "failed"
        message: str | None = None
        try:
            try:
                await run.events.emit(
                    "turn.started",
                    {"turn_id": turn_id, "round_no": run.round_no, "model": turn["model"]},
                )
                run.agent = await assistant_runtime.resolve_agent_runtime_async(
                    snapshot=turn.get("execution_snapshot") or None
                )
                main = asyncio.create_task(run.model.run_loop(), name=f"agent-loop-{turn_id}")
                watcher = asyncio.create_task(_cancel_watcher(run, main), name=f"agent-cancel-{turn_id}")
                try:
                    await main
                finally:
                    await _stop_task(watcher)
                status = "completed"
            except TurnCancelled:
                status = "cancelled"
            except asyncio.CancelledError:
                if run._lease_lost:
                    status = "interrupted"
                    message = "The Agent turn lost its execution lease."
                elif run._cancelled:
                    status = "cancelled"
                else:
                    await asyncio.shield(
                        run.lifecycle.finalize("interrupted", "The server stopped before the turn finished.")
                    )
                    raise
            except Exception as error:
                message = describe_failure(error)
            if status == "failed":
                try:
                    await run.events.add_error_block(message or "The Agent turn failed.")
                except Exception:
                    logger.warning("Agent turn %s could not record its error block", turn_id, exc_info=True)
            await _stop_task(flusher)
            await _stop_task(renewer)
            finalizer = asyncio.create_task(run.lifecycle.finalize(status, message))
            try:
                await asyncio.shield(finalizer)
            except asyncio.CancelledError:
                await asyncio.shield(finalizer)
                raise
        finally:
            await _stop_task(renewer)
            await _stop_task(flusher)
            state.agent_turn_wakeups.pop(turn_id, None)
            state.agent_turn_cancel_events.pop(turn_id, None)

