"""Single owner of Agent blocks, text deltas and durable event snapshots."""

import asyncio
import copy
import time
from typing import TYPE_CHECKING, Any

from ..repositories import agent as agent_repo

if TYPE_CHECKING:
    from .agent_run_context import AgentRunContext

TEXT_FLUSH_CHARS = 256
TEXT_FLUSH_SECONDS = 0.15
PERSIST_INTERVAL_SECONDS = 1.0
EVENT_FLUSH_MAX = 16
TERMINAL_EVENT_TYPES = frozenset({"turn.completed", "turn.failed", "turn.cancelled"})


class AgentEventWriter:
    def __init__(self, run: "AgentRunContext") -> None:
        self.run = run
        self.blocks: list[dict[str, Any]] = []
        self._block_seq = 0
        self._text_block: dict[str, Any] | None = None
        self._pending_text = ""
        self._last_text_emit = time.monotonic()
        self._last_persist = 0.0
        self._revision = 0
        self._saved_revision = 0
        self._pending_events: list[tuple[str, dict[str, Any]]] = []
        self._next_event_seq: int | None = None
        self._emit_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()

    @property
    def text_block(self) -> dict[str, Any] | None:
        return self._text_block

    def begin_text_segment(self) -> None:
        self._text_block = None

    async def ensure_text_block(self) -> dict[str, Any]:
        if self._text_block is None:
            self._text_block = {"id": self.next_block_id("t"), "type": "text", "text": ""}
            await self.upsert_block(self._text_block)
        return self._text_block

    def next_block_id(self, prefix: str) -> str:
        self._block_seq += 1
        return f"{prefix}{self._block_seq}"

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Buffer one event; `_event_flush_loop` and `flush_events` write it out.

        Only the top level is snapshotted: callers must not mutate nested
        containers after emitting (block upserts pass a fresh ``dict(block)``),
        which is what the previous deepcopy protected against.
        """
        async with self._emit_lock:
            if self.run._lease_lost:
                return
            self._pending_events.append((event_type, dict(data)))
            if len(self._pending_events) >= EVENT_FLUSH_MAX:
                await self._flush_events_locked()

    async def flush_events(self) -> None:
        async with self._emit_lock:
            await self._flush_events_locked()

    async def _flush_events_locked(self) -> None:
        if self.run._lease_lost:
            self._pending_events.clear()
            return
        if not self._pending_events:
            return
        if self._next_event_seq is None:
            self._next_event_seq = (
                await self.run._db(
                    agent_repo.read_event_cursor,
                    self.run.turn_id,
                    metric_name="agent_read_event_cursor",
                )
            ) + 1
        events, self._pending_events = self._pending_events, []
        try:
            last = await self.run._db(
                agent_repo.append_turn_events,
                self.run.turn_id,
                events,
                start_seq=self._next_event_seq,
                metric_name="agent_append_turn_events",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Put the batch back so a later flush can retry it.
            self._pending_events = events + self._pending_events
            raise
        self._next_event_seq = last + 1
        self.run._wakeup.set()

    async def maybe_persist(self) -> None:
        if self._saved_revision == self._revision or self.run._lease_lost:
            return
        if time.monotonic() - self._last_persist >= PERSIST_INTERVAL_SECONDS:
            await self.persist()

    async def upsert_block(self, block: dict[str, Any]) -> None:
        self._revision += 1
        for index, existing in enumerate(self.blocks):
            if existing["id"] == block["id"]:
                self.blocks[index] = block
                break
        else:
            self.blocks.append(block)
        await self.emit("block.upsert", {"block": dict(block)})
        # Snapshot writes are coalesced with the persist cadence: a reload
        # mid-turn reads at most one interval behind the event stream.
        await self.maybe_persist()

    async def add_text(self, text: str) -> None:
        if not text:
            return
        block = await self.ensure_text_block()
        block["text"] += text
        self._revision += 1
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
            if self.run._lease_lost:
                return
            revision = self._revision
            text = "\n\n".join(
                block["text"] for block in self.blocks if block["type"] == "text" and block["text"].strip()
            )
            await self.run._db(
                agent_repo.update_message_content,
                self.run.message_id,
                text=text,
                blocks=copy.deepcopy(self.blocks),
                metric_name="agent_update_message",
            )
            self._saved_revision = revision
            self._last_persist = time.monotonic()

    async def add_error_block(self, message: str) -> None:
        await self.flush_text()
        await self.upsert_block({"id": self.next_block_id("e"), "type": "error", "message": message})

