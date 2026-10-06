"""Start and track Agent turns; execution responsibilities live in dedicated owners."""

import asyncio

from ..runtime.state import state
from .agent_run_context import AgentRunContext
from .agent_run_lifecycle import AgentRunLifecycle


async def run_turn(turn_id: str) -> None:
    await AgentRunLifecycle.execute(turn_id, AgentRunContext)


def wake_turn_cancel(turn_id: str) -> None:
    """Tell a turn running in this process that its cancel flag was just set.

    The DB flag stays the source of truth (and the only signal across workers,
    which poll it); this only removes the polling delay when the cancel request
    lands on the worker that runs the turn.
    """
    event = state.agent_turn_cancel_events.get(turn_id)
    if event is not None:
        event.set()


def spawn_turn(turn_id: str) -> asyncio.Task:
    tasks = state.agent_turn_tasks
    task = asyncio.create_task(run_turn(turn_id), name=f"agent-turn-{turn_id}")
    tasks[turn_id] = task
    task.add_done_callback(lambda _task: tasks.pop(turn_id, None))
    return task
