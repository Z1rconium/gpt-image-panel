"""Shared local fencing state for a claim a worker holds.

``LeaseHolder`` owns the lost-flag event and the local monotonic lease
deadline. The deadline is pushed forward on every successful write that extends
the lease, and the renewal loop reads it to decide how long a failing renewal
may keep retrying before the work must be abandoned. Domain callers supply the
exception type raised on lease loss, so the shared state stays free of
image/gallery-specific error and metric concerns.
"""

from __future__ import annotations

import asyncio
import time


class LeaseLostError(RuntimeError):
    """The held lease moved to another owner (or was interrupted)."""


class LeaseHolder:
    """Fencing state for one claimed lease."""

    def __init__(self, *, lease_seconds: float) -> None:
        self.lease_seconds = float(lease_seconds)
        self.lease_lost = asyncio.Event()
        self.deadline = time.monotonic() + self.lease_seconds

    def note_lease_extended(self, anchor: float | None = None) -> None:
        """Record a lease extension, anchored at the time the new expiry was
        computed (before the DB write) so the local deadline never runs ahead
        of the value actually stored."""
        anchored_at = time.monotonic() if anchor is None else anchor
        self.deadline = anchored_at + self.lease_seconds

    def raise_if_lease_lost(
        self,
        error_cls: type[BaseException] = LeaseLostError,
    ) -> None:
        if self.lease_lost.is_set():
            raise error_cls()

    def mark_lease_lost(self) -> bool:
        """Signal lease loss; return True only when this call first set the flag."""
        if self.lease_lost.is_set():
            return False
        self.lease_lost.set()
        return True
