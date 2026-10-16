"""SQLite execution policy and database-aware executor shutdown.

The runtime owns worker pools; this adapter owns repository connection scopes,
lock retries and worker connection cleanup.
"""

import asyncio
import logging
import random
import sqlite3
import time
from collections.abc import Callable
from typing import Any, TypeVar

from ...core import settings as config
from ...core.observability import metrics
from ...runtime.blocking import (
    close_blocking_executors as _close_blocking_executors,
    run_db_callback,
    run_on_db_workers,
)
from .connection import (
    _close_thread_connection,
    busy_timeout_scope,
    metric_name_scope,
    persistent_connection_scope,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _is_sqlite_busy(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower() for marker in ("locked", "busy")
    )


def _resolve_busy_policy(critical: bool, retry_busy: bool) -> tuple[int, int]:
    """Return ``(busy_timeout_ms, attempts)`` for one repository operation.

    ``critical`` selects the larger critical busy budget used by claim, lease
    renewal, terminal unit writes, parent finalization and cancellation. Those
    paths must not silently lose a write to a transient lock; polling reads keep
    the short budget because timing out just skips a cycle.
    """
    if critical:
        busy_timeout_ms = config.SQLITE_CRITICAL_BUSY_TIMEOUT_MS
        attempts = config.SQLITE_CRITICAL_BUSY_RETRY_ATTEMPTS if retry_busy else 0
    else:
        busy_timeout_ms = config.SQLITE_BUSY_TIMEOUT_MS
        attempts = config.SQLITE_BUSY_RETRY_ATTEMPTS if retry_busy else 0
    return busy_timeout_ms, attempts


async def run_db_operation(
    callback: Callable[..., T],
    *args: Any,
    metric_name: str | None = None,
    retry_busy: bool = True,
    critical: bool = False,
    **kwargs: Any,
) -> T:
    """Run one repository operation with a short lock timeout and jittered retry."""

    label = metric_name or getattr(callback, "__name__", "operation")
    busy_timeout_ms, attempts = _resolve_busy_policy(critical, retry_busy)

    def invoke() -> T:
        with metric_name_scope(label):
            with persistent_connection_scope(busy_timeout_ms):
                return callback(*args, **kwargs)

    for attempt in range(attempts + 1):
        try:
            return await run_db_callback(invoke, metric_name=f"db.{label}")
        except BaseException as error:
            if not _is_sqlite_busy(error) or attempt >= attempts:
                raise
            metrics.increment("sqlite.busy_retries")
            base = config.SQLITE_BUSY_RETRY_BASE_MS / 1000
            await asyncio.sleep(base * (2**attempt) * random.uniform(0.75, 1.25))
    raise RuntimeError("unreachable")


def run_db_operation_in_current_thread(
    callback: Callable[..., T],
    *args: Any,
    metric_name: str | None = None,
    retry_busy: bool = True,
    critical: bool = False,
    **kwargs: Any,
) -> T:
    """Run one repository operation in the current worker thread.

    This mirrors run_db_operation's short SQLite busy timeout and jittered retry
    policy for callbacks that are already executing off the event loop.
    """

    label = metric_name or getattr(callback, "__name__", "operation")
    busy_timeout_ms, attempts = _resolve_busy_policy(critical, retry_busy)
    for attempt in range(attempts + 1):
        started_at = time.perf_counter()
        try:
            with metric_name_scope(label):
                with busy_timeout_scope(busy_timeout_ms):
                    return callback(*args, **kwargs)
        except BaseException as error:
            if not _is_sqlite_busy(error) or attempt >= attempts:
                raise
            metrics.increment("sqlite.busy_retries")
            base = config.SQLITE_BUSY_RETRY_BASE_MS / 1000
            time.sleep(base * (2**attempt) * random.uniform(0.75, 1.25))
        finally:
            metrics.observe_ms(
                f"db.{label}",
                (time.perf_counter() - started_at) * 1000,
            )

    raise RuntimeError("unreachable")


async def close_blocking_executors() -> None:
    closed_workers = await asyncio.to_thread(
        run_on_db_workers,
        _close_thread_connection,
    )
    expected_workers = max(1, int(config.DB_EXECUTOR_WORKERS))
    if closed_workers < expected_workers:
        logger.warning(
            "Closed %s of %s database worker connections at shutdown; the rest "
            "close when their threads exit",
            closed_workers,
            expected_workers,
        )
    await _close_blocking_executors()


__all__ = [
    "close_blocking_executors",
    "run_db_operation",
    "run_db_operation_in_current_thread",
]
