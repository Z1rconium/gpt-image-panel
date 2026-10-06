"""Identity, execution policy and fencing signals shared by an Agent run."""

import asyncio
from typing import Any

from ..repositories import agent as agent_repo
from ..runtime.blocking import run_db_operation
from ..runtime.state import state
from . import agent_search, assistant_runtime
from .agent_event_writer import AgentEventWriter
from .agent_model_round import AgentModelRound
from .agent_run_lifecycle import AgentRunLifecycle
from .agent_tool_executor import AgentToolExecutor
from .agent_turn_errors import TurnCancelled


class AgentRunContext:
    def __init__(self, turn: dict[str, Any]) -> None:
        self.turn = turn
        self.turn_id: str = turn["id"]
        self.conversation_id: str = turn["conversation_id"]
        self.round_no = int(turn["round_no"])
        self.message_id: str = turn["assistant_message_id"]
        self.path_round_no = 0
        self.agent: assistant_runtime.AgentRuntime | None = None
        self.rounds_used = 0
        self._lease_lost = False
        self._main_task: asyncio.Task | None = None
        self._finishing = False
        self._finalized = False
        self._cancelled = False
        self._wakeup = state.agent_turn_wakeups.setdefault(self.turn_id, asyncio.Event())
        self.search = agent_search.TurnSearch()
        self.events = AgentEventWriter(self)
        self.tools = AgentToolExecutor(self)
        self.model = AgentModelRound(self)
        self.lifecycle = AgentRunLifecycle(self)

    async def _db(self, callback, *args, **kwargs):
        owned = {
            agent_repo.append_turn_events, agent_repo.update_message_content,
            agent_repo.set_turn_rounds_used, agent_repo.insert_pending_output_image,
            agent_repo.set_image_job, agent_repo.settle_image,
        }
        if callback in owned:
            kwargs["owner"] = self.turn.get("lease_owner") if not self._finalized else None
        try:
            return await run_db_operation(callback, *args, **kwargs)
        except agent_repo.AgentLeaseLostError:
            self._lease_lost = True
            if not self._finishing and self._main_task is not None and self._main_task is not asyncio.current_task():
                self._main_task.cancel()
            raise asyncio.CancelledError()

    async def check_cancel(self) -> None:
        """Cooperative check; the DB polling lives in `_cancel_watcher`."""
        if self._cancelled:
            raise TurnCancelled()
        if self._lease_lost:
            raise asyncio.CancelledError()

