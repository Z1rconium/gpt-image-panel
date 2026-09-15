"""Adaptive idle backoff for fixed-interval DB polling loops.

SSE pollers query SQLite to catch job updates written by *other* processes.
Same-process updates are already pushed immediately by the in-process job event
bus, so a poller only needs to stay at its base cadence while something is
actually changing. Doubling the delay while idle stops a connected-but-idle UI
from holding a DB worker at a fixed multiple-queries-per-second rate.
"""

from __future__ import annotations


def next_poll_delay(
    *,
    base_interval: float,
    current_delay: float,
    changed: bool,
    max_backoff_seconds: float,
) -> float:
    """Return the sleep to use before the next poll.

    Observed changes reset the cadence to ``base_interval`` so active work is
    picked up exactly as fast as before; an unchanged cycle doubles the delay
    up to ``max_backoff_seconds``.
    """
    base = max(0.0, float(base_interval))
    cap = max(base, float(max_backoff_seconds))
    if changed:
        return base
    return min(cap, max(base, float(current_delay) * 2))
