"""Lease, progress persistence and upstream-task lifecycle of one claimed image unit.

``ImageUnitSession`` is the single owner of the fencing state for a unit this
worker has claimed: whether the lease is lost, the locally tracked lease
deadline, the coalesced progress writer, and the renewal/upstream tasks. The
executor drives the generation logic and consults the session; it never
touches those fields directly.
"""

import asyncio
from collections.abc import Awaitable, Callable
import logging
import time
from datetime import datetime, timedelta, timezone

from ..core import settings as config
from ..core.observability import metrics
from ..repositories.image_jobs import renew_image_job_unit_lease, update_image_job_unit_progress
from ..runtime.blocking import run_db_operation
from .image_job_aggregate import aggregate_parent_image_job, set_generate_job_progress

logger = logging.getLogger(__name__)
# Cadence for retrying a lease renewal that failed with a DB error, while the
# locally tracked lease is still valid.
IMAGE_UNIT_LEASE_RENEW_RETRY_SECONDS = 5.0


class UnitLeaseLostError(Exception):
    """Raised when this worker's claim token no longer owns the image unit.

    Ownership has moved to a newer claim (or the session was interrupted), so
    the executor must stop the in-flight upstream request and must not write a
    terminal state for the unit.
    """


def datetime_from_monotonic_delta(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1.0, seconds))).isoformat()


def image_unit_lease_expires_at() -> str:
    return datetime_from_monotonic_delta(config.IMAGE_JOB_UNIT_LEASE_SECONDS)


def image_unit_lease_renew_interval() -> float:
    """Renewal cadence for an in-flight image unit.

    Trusts the configured value when it is safely below `lease/2`; otherwise
    falls back to `lease/4` so a renewal can always land before expiry.
    """
    lease = float(config.IMAGE_JOB_UNIT_LEASE_SECONDS)
    renew = float(config.IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS)
    if renew <= 0 or renew >= lease / 2:
        renew = lease / 4
    return max(0.1, renew)


class ImageUnitSession:
    def __init__(
        self,
        *,
        unit_id: str,
        parent_job_id: str,
        operation: str,
        claim_token: str,
        worker_id: str,
    ) -> None:
        self.unit_id = unit_id
        self.parent_job_id = parent_job_id
        self.operation = operation
        self.claim_token = claim_token
        self.worker_id = worker_id
        self.lease_lost = asyncio.Event()
        self.lease_task: asyncio.Task | None = None
        self.upstream_task: asyncio.Task | None = None
        self._progress_pending: tuple[str, str] | None = None
        self._progress_task: asyncio.Task | None = None
        self._last_progress_persist_at = 0.0
        self._last_aggregate_at = 0.0
        # Local view of when the SQLite lease expires. Every successful write that
        # sets `claim_expires_at` (start progress, coalesced progress, renewal)
        # pushes it forward; the renewal loop uses it to decide how long a failing
        # renewal may keep retrying before the unit must be abandoned.
        self._lease_deadline = time.monotonic() + float(config.IMAGE_JOB_UNIT_LEASE_SECONDS)

    # ── lease ──────────────────────────────────────────────────

    def note_lease_extended(self, anchor: float | None = None) -> None:
        """Record a lease extension, anchored at the time the new expiry was
        computed (before the DB write) so the local deadline never runs ahead
        of the value actually stored in SQLite."""
        anchored_at = time.monotonic() if anchor is None else anchor
        self._lease_deadline = anchored_at + float(config.IMAGE_JOB_UNIT_LEASE_SECONDS)

    def raise_if_lease_lost(self) -> None:
        if self.lease_lost.is_set():
            raise UnitLeaseLostError()

    def mark_lease_lost(self) -> None:
        if self.lease_lost.is_set():
            return
        self.lease_lost.set()
        metrics.increment("image_jobs.lease_lost")

    async def abort_upstream(self) -> None:
        """Cancel the in-flight upstream call and wait for it to unwind.

        `asyncio.wait` does not cancel its members, so an outer cancellation
        (graceful shutdown) or a lost lease must stop the upstream task
        explicitly; otherwise the request keeps running and its late progress
        writes are misreported as lease loss.
        """
        task = self.upstream_task
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def run_upstream(self, call: Callable[[], Awaitable[list]]) -> list:
        self.lease_task = asyncio.create_task(self.renew_lease_loop())
        self.upstream_task = asyncio.create_task(call())
        await asyncio.wait(
            {self.upstream_task, self.lease_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self.lease_lost.is_set() or self.lease_task.done():
            if self.lease_task.done() and not self.lease_task.cancelled():
                self.lease_task.exception()
            self.mark_lease_lost()
            await self.abort_upstream()
            raise UnitLeaseLostError()
        # Lease loop keeps running until upstream completes; cancel it.
        self.lease_task.cancel()
        entries = await self.upstream_task
        return entries

    async def close(self) -> None:
        await self.abort_upstream()
        if self.lease_task is not None:
            if not self.lease_task.done():
                self.lease_task.cancel()
            elif not self.lease_task.cancelled():
                self.lease_task.exception()
        if self.lease_task is not None:
            await asyncio.gather(self.lease_task, return_exceptions=True)
        await self.flush_progress_updates()

    async def renew_lease_loop(self) -> None:
        """Periodically extend the unit lease while the upstream call runs.

        A `False` return (or a fencing check failure from any other write
        path) means ownership moved elsewhere, so signal lease loss right away.
        A DB error is different: the lease is still ours until `_lease_deadline`,
        so retry on a short cadence and only give up once another retry could
        no longer land before expiry. Aborting an expensive upstream call over
        one transient SQLite stall would burn an attempt for nothing.
        """
        renew_interval = image_unit_lease_renew_interval()
        retry_interval = min(renew_interval, IMAGE_UNIT_LEASE_RENEW_RETRY_SECONDS)
        delay = renew_interval
        while True:
            await asyncio.sleep(delay)
            if self.lease_lost.is_set():
                return
            renew_started_at = time.monotonic()
            try:
                renewed = await run_db_operation(
                    renew_image_job_unit_lease,
                    self.unit_id,
                    claim_token=self.claim_token,
                    claim_expires_at=image_unit_lease_expires_at(),
                    metric_name="renew_image_job_unit_lease",
                    critical=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                remaining = self._lease_deadline - time.monotonic()
                if remaining <= retry_interval:
                    logger.exception(
                        "Image unit lease renewal failed and the lease is about "
                        "to expire, abandoning unit: unit_id=%s parent_job_id=%s "
                        "worker_id=%s remaining=%.1fs",
                        self.unit_id,
                        self.parent_job_id,
                        self.worker_id,
                        remaining,
                    )
                    self.mark_lease_lost()
                    return
                logger.warning(
                    "Image unit lease renewal failed, retrying in %.1fs "
                    "(lease valid for %.1fs): unit_id=%s parent_job_id=%s worker_id=%s",
                    retry_interval,
                    remaining,
                    self.unit_id,
                    self.parent_job_id,
                    self.worker_id,
                    exc_info=True,
                )
                metrics.increment("image_jobs.lease_renew_retry")
                delay = retry_interval
                continue
            if renewed:
                self.note_lease_extended(renew_started_at)
                metrics.increment("image_jobs.lease_renewed")
                delay = renew_interval
                continue
            logger.warning(
                "Image unit lease lost: unit_id=%s parent_job_id=%s worker_id=%s",
                self.unit_id,
                self.parent_job_id,
                self.worker_id,
            )
            self.mark_lease_lost()
            return

    # ── progress ───────────────────────────────────────────────

    async def _persist_progress_updates(self) -> None:
        try:
            while self._progress_pending is not None:
                delay = config.IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS - (
                    time.monotonic() - self._last_progress_persist_at
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                stage, message = self._progress_pending
                self._progress_pending = None
                persist_started_at = time.monotonic()
                updated = await run_db_operation(
                    update_image_job_unit_progress,
                    self.unit_id,
                    claim_token=self.claim_token,
                    stage=stage,
                    message=message,
                    claim_expires_at=image_unit_lease_expires_at(),
                    metric_name="persist_image_job_progress",
                )
                if updated is None:
                    logger.warning(
                        "Image unit progress rejected, lease lost: "
                        "unit_id=%s parent_job_id=%s worker_id=%s",
                        self.unit_id,
                        self.parent_job_id,
                        self.worker_id,
                    )
                    self.mark_lease_lost()
                    return
                self.note_lease_extended(persist_started_at)
                now = time.monotonic()
                self._last_progress_persist_at = now
                if now - self._last_aggregate_at >= config.IMAGE_JOB_AGGREGATE_MIN_INTERVAL_SECONDS:
                    self._last_aggregate_at = now
                    await aggregate_parent_image_job(self.parent_job_id)
        finally:
            self._progress_task = None
            if self._progress_pending is not None and not self.lease_lost.is_set():
                self._progress_task = asyncio.create_task(self._persist_progress_updates())

    def progress(self, stage: str, message: str) -> None:
        set_generate_job_progress(self.parent_job_id, stage, message, self.operation)
        if self.lease_lost.is_set():
            return
        self._progress_pending = (stage, message)
        if self._progress_task is None or self._progress_task.done():
            self._progress_task = asyncio.create_task(self._persist_progress_updates())

    async def flush_progress_updates(self) -> None:
        task = self._progress_task
        if task is not None:
            await asyncio.gather(task, return_exceptions=False)
        while self._progress_task is not None:
            task = self._progress_task
            await asyncio.gather(task, return_exceptions=False)

    async def flush_progress_before_terminal(self, *, suppress_cancelled: bool) -> None:
        """Drain pending progress writes before a terminal unit write.

        Progress persistence must finish before the unit leaves `running`;
        otherwise a late progress write after the terminal write sees rowcount 0
        and is misread as a lost lease. In the graceful-cancellation path the
        nested CancelledError is expected and suppressed so the cancelled
        terminal state can still be written; elsewhere it is re-raised so we do
        not mask an outer cancellation.
        """
        try:
            await self.flush_progress_updates()
        except asyncio.CancelledError:
            if not suppress_cancelled:
                raise
            logger.debug(
                "Progress flush interrupted before terminal write: unit_id=%s",
                self.unit_id,
            )
        except Exception:
            logger.exception(
                "Progress flush failed before terminal write: unit_id=%s",
                self.unit_id,
            )
