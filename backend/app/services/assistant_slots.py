"""Global AI Assistant request-slot leasing (F-G6-1).

Split out of :mod:`app.services.assistant_runtime`, which now owns only endpoint
resolution. A slot is a database-backed background lease (one per concurrent
request) plus an in-process semaphore; the renewal loop is the shared
:func:`app.services.lease_renewal.renew_lease_until_lost`.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from ..core import settings as config
from ..core.errors import DomainError, RateLimitedError
from ..core.utils import utc_now
from ..integrations import agent_client
from ..repositories.coordination import (
    acquire_background_slot,
    release_background_slot,
    renew_background_slot,
)
from ..repositories.db.execution import run_db_operation
from ..runtime.state import state, utc_lease_expires_at
from .lease_renewal import renew_lease_until_lost

logger = logging.getLogger(__name__)

AI_ASSISTANT_SLOT_RETRY_SECONDS = 1.0
ASSISTANT_SLOT_CLEANUP_MARGIN_SECONDS = 30


def _assistant_request_semaphore() -> asyncio.Semaphore:
    limit = max(1, int(config.AI_ASSISTANT_MAX_CONCURRENCY or 1))
    cached = getattr(state, "assistant_request_semaphore_state", None)
    if not cached or cached[0] != limit:
        semaphore = asyncio.Semaphore(limit)
        state.assistant_request_semaphore_state = (limit, semaphore)
        return semaphore
    return cached[1]


def _assistant_slot_lease_seconds(timeout_seconds: int) -> int:
    """Lease covers the whole request deadline plus cleanup; renewal extends it further."""
    timeout = int(timeout_seconds or config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS)
    total = agent_client.request_total_timeout_seconds(timeout)
    return int(total) + ASSISTANT_SLOT_CLEANUP_MARGIN_SECONDS


def _assistant_slot_expires_at(timeout_seconds: int) -> str:
    return utc_lease_expires_at(_assistant_slot_lease_seconds(timeout_seconds))


def _assistant_slot_owner() -> str:
    worker_id = state.worker_id
    return f"{worker_id}-{os.urandom(8).hex()}"


async def _acquire_assistant_slot(timeout_seconds: int) -> tuple[str, str]:
    owner = _assistant_slot_owner()
    slot_name = await run_db_operation(
        acquire_background_slot,
        name_prefix="ai_assistant_request",
        owner=owner,
        slot_count=config.AI_ASSISTANT_MAX_CONCURRENCY,
        lease_expires_at=_assistant_slot_expires_at(timeout_seconds),
        now=utc_now(),
    )
    if not slot_name:
        raise RateLimitedError("AI Assistant is at its concurrency limit")
    return slot_name, owner


async def _release_assistant_slot(slot_name: str, owner: str) -> None:
    try:
        await run_db_operation(release_background_slot,
            name=slot_name,
            owner=owner,
            now=utc_now(),
        )
    except Exception:
        logger.warning("Failed to release AI Assistant concurrency slot %s", slot_name, exc_info=True)


async def _renew_assistant_slot_loop(
    slot_name: str, owner: str, timeout_seconds: int, on_lost
) -> None:
    lease_seconds = _assistant_slot_lease_seconds(timeout_seconds)
    interval = max(1.0, lease_seconds / 3)

    async def renew() -> bool:
        return bool(
            await run_db_operation(
                renew_background_slot,
                name=slot_name,
                owner=owner,
                lease_expires_at=utc_lease_expires_at(lease_seconds),
                now=utc_now(),
            )
        )

    def on_transient_error(exc: Exception, remaining: float) -> None:
        logger.warning("Assistant slot renewal failed for %s", slot_name, exc_info=True)

    def on_lost_and_log() -> None:
        logger.warning("Assistant slot %s was lost; aborting the request", slot_name)
        on_lost()

    await renew_lease_until_lost(
        renew,
        interval_seconds=interval,
        retry_interval_seconds=interval,
        on_lost=on_lost_and_log,
        on_transient_error=on_transient_error,
    )


@asynccontextmanager
async def _assistant_request_limit(
    timeout_seconds: int,
    *,
    wait_for_slot: bool = False,
    max_wait_seconds: float | None = None,
):
    """Hold one global assistant request slot.

    ``wait_for_slot`` retries a saturated slot pool instead of failing; callers
    that must not wait forever (Agent turns) pass ``max_wait_seconds`` so the
    retry loop ends with a 429 instead of holding their own capacity indefinitely.
    """
    wait_deadline = (
        time.monotonic() + max_wait_seconds
        if wait_for_slot and max_wait_seconds is not None
        else None
    )
    async def before_deadline(awaitable):
        if wait_deadline is None:
            return await awaitable
        return await asyncio.wait_for(awaitable, max(0, wait_deadline - time.monotonic()))

    while True:
        semaphore = _assistant_request_semaphore()
        try:
            await before_deadline(semaphore.acquire())
        except asyncio.TimeoutError as error:
            raise DomainError("AI Assistant is busy. Try again shortly.", status_code=429) from error
        acquisition = asyncio.create_task(_acquire_assistant_slot(timeout_seconds))
        slot = None
        retry = False
        try:
            try:
                slot = await before_deadline(asyncio.shield(acquisition))
            except (asyncio.CancelledError, asyncio.TimeoutError) as error:
                # Leave the worker operation alive and release any late lease.
                async def release_late():
                    try:
                        late = await acquisition
                        await _release_assistant_slot(*late)
                    except Exception:
                        logger.warning("Assistant late slot acquisition failed", exc_info=True)
                cleanup = asyncio.create_task(release_late())
                cleanups = state.assistant_slot_cleanup_tasks
                cleanups.add(cleanup)
                cleanup.add_done_callback(cleanups.discard)
                cleanup.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                if isinstance(error, asyncio.TimeoutError):
                    raise DomainError("AI Assistant is busy. Try again shortly.", status_code=429) from error
                raise
            except DomainError as error:
                if not wait_for_slot or error.status_code != 429:
                    raise
                retry = True
            if not retry:
                lost = False
                host = asyncio.current_task()

                def on_lost() -> None:
                    nonlocal lost
                    lost = True
                    if host is not None:
                        host.cancel()

                renewer = asyncio.create_task(_renew_assistant_slot_loop(*slot, timeout_seconds, on_lost))
                try:
                    yield
                except asyncio.CancelledError:
                    if lost:
                        raise DomainError("AI Assistant concurrency slot was lost.", status_code=503) from None
                    raise
                finally:
                    renewer.cancel()
                return
        finally:
            try:
                if slot is not None:
                    await asyncio.shield(_release_assistant_slot(*slot))
            finally:
                semaphore.release()
        try:
            await before_deadline(asyncio.sleep(AI_ASSISTANT_SLOT_RETRY_SECONDS))
        except asyncio.TimeoutError as error:
            raise DomainError("AI Assistant is busy. Try again shortly.", status_code=429) from error


assistant_request_limit = _assistant_request_limit

__all__ = [name for name in globals() if not name.startswith("__")]
