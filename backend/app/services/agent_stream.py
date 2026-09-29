"""Outbound SSE for an Agent turn, tailing the persisted turn events."""

import asyncio
import logging
import time
from typing import Any

from ..core import settings as config
from ..core.errors import NotFoundError, RateLimitedError
from ..core.streaming import StreamedBody
from ..repositories import agent as agent_repo
from ..repositories.sse_limiter import sse_limiter
from ..runtime.blocking import run_db_operation
from ..runtime.state import state
from .agent_turns import TERMINAL_EVENT_TYPES
from .gallery_common import PRIVATE_GALLERY_CACHE_CONTROL
from .job_events import serialize_sse_event

logger = logging.getLogger(__name__)

EVENT_POLL_SECONDS = 0.4
KEEP_ALIVE_SECONDS = 15.0
STALE_SWEEP_SECONDS = 30.0
EVENT_BATCH = 200


def _terminal_event_for(turn: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    status = turn["status"]
    if status == "completed":
        return "turn.completed", {"rounds_used": turn["rounds_used"]}
    if status == "cancelled":
        return "turn.cancelled", {}
    return "turn.failed", {"message": turn.get("error_message") or "The Agent turn failed."}


async def stream_turn_events(
    *,
    turn_id: str,
    after: int,
    client_ip: str,
    is_disconnected,
) -> StreamedBody:
    turn = await run_db_operation(agent_repo.get_turn, turn_id, metric_name="agent_get_turn")
    if turn is None:
        raise NotFoundError("Agent turn not found")
    sse_lease = await sse_limiter.acquire(client_ip)
    if not sse_lease:
        raise RateLimitedError("Too many SSE connections")

    async def event_stream():
        start = time.monotonic()
        last_refresh_at = start
        last_sent = start
        last_sweep = start
        cursor = max(0, int(after))
        try:
            while True:
                if await is_disconnected():
                    return
                refreshed_at = await sse_limiter.refresh_if_needed(sse_lease, last_refresh_at)
                if refreshed_at is None:
                    return
                last_refresh_at = refreshed_at
                events = await run_db_operation(
                    agent_repo.list_turn_events,
                    turn_id,
                    after_seq=cursor,
                    limit=EVENT_BATCH,
                    metric_name="agent_list_turn_events",
                )
                for event in events:
                    cursor = event["seq"]
                    last_sent = time.monotonic()
                    yield serialize_sse_event(event["type"], event["data"], event_id=cursor)
                    if event["type"] in TERMINAL_EVENT_TYPES:
                        return
                if events:
                    continue
                current = await run_db_operation(agent_repo.get_turn, turn_id, metric_name="agent_get_turn")
                if current is None:
                    return
                if current["status"] in agent_repo.TERMINAL_TURN_STATUSES:
                    # Terminal with nothing left to replay: its events were purged or
                    # the turn was interrupted by a stale-lease sweep.
                    event_type, data = _terminal_event_for(current)
                    yield serialize_sse_event(event_type, data, event_id=cursor + 1)
                    return
                now = time.monotonic()
                if now - start > config.SSE_CONNECTION_TTL_SECONDS:
                    return
                if now - last_sweep >= STALE_SWEEP_SECONDS:
                    last_sweep = now
                    await run_db_operation(agent_repo.sweep_stale_turns, metric_name="agent_sweep_stale_turns")
                if now - last_sent >= KEEP_ALIVE_SECONDS:
                    last_sent = now
                    yield ": keep-alive\n\n"
                # The runner owns the wake-up event; a turn running on another worker
                # has none here, so fall back to plain polling.
                wakeup = state.agent_turn_wakeups.get(turn_id)
                if wakeup is None:
                    await asyncio.sleep(EVENT_POLL_SECONDS)
                    continue
                wakeup.clear()
                try:
                    await asyncio.wait_for(wakeup.wait(), timeout=EVENT_POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient backend failure closes the stream; EventSource then
            # reconnects and resumes from Last-Event-ID.
            logger.exception("Agent event stream for turn %s failed", turn_id)
        finally:
            await sse_limiter.release(sse_lease)

    return StreamedBody(
        chunks=event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": PRIVATE_GALLERY_CACHE_CONTROL,
            "X-Accel-Buffering": "no",
        },
    )
