"""Shared periodic lease/slot renewal loop (F-G5-3).

Several workers hold a lease (an image unit, an Agent turn, a NodeImage upload
job, an AI Assistant request slot) and must extend it on a cadence while the
real work runs, aborting immediately if ownership moves elsewhere. They used to
re-implement the same ``sleep -> renew -> if not renewed: abandon`` skeleton
five times, so every fix (backoff, cancellation, deadline handling) had to be
applied five times and had already drifted.

:func:`renew_lease_until_lost` owns the skeleton; each caller supplies the
renew callable, cadence, lost handler, and its transient-error policy. The
bounded ``_sleep_while_renewing_background_lease`` helper is *not* this shape
(it renews while sleeping for a fixed delay and returns a bool), so it stays
as-is.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable


async def renew_lease_until_lost(
    renew: Callable[[], Awaitable[bool]],
    *,
    interval_seconds: float,
    on_lost: Callable[[], None],
    stop_event: asyncio.Event | None = None,
    retry_interval_seconds: float | None = None,
    deadline: Callable[[], float] | None = None,
    renew_timeout: Callable[[], float] | None = None,
    on_renewed: Callable[[float], None] | None = None,
    on_transient_error: Callable[[Exception, float], None] | None = None,
    keep_going: Callable[[], bool] | None = None,
) -> None:
    """Renew a lease on a cadence until it is lost (or the task is cancelled).

    ``renew`` returns ``True`` when the lease was extended and ``False`` when
    ownership moved, in which case ``on_lost`` runs and the loop returns.
    ``asyncio.CancelledError`` always propagates.

    - ``stop_event``: when given, wait on it (returning cleanly once set) rather
      than sleeping, so the owner can stop renewal without cancelling the task.
    - ``retry_interval_seconds``: retry transient exceptions on this cadence
      instead of letting them escape. ``None`` re-raises.
    - ``deadline``: monotonic expiry; a transient error once the remaining time
      no longer covers ``retry_interval_seconds`` calls ``on_lost`` (the next
      retry could not land before expiry).
    - ``renew_timeout``: wrap each ``renew`` in ``asyncio.wait_for`` with this
      timeout, for deadline-anchored renewals.
    """
    delay = interval_seconds
    while True:
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(0.0, delay))
                return
            except asyncio.TimeoutError:
                pass
        else:
            # Never sleep past the lease deadline: an already/nearly expired
            # lease must be renewed (or abandoned) immediately.
            sleep_for = delay
            if deadline is not None:
                sleep_for = min(delay, max(0.0, deadline() - time.monotonic()))
            await asyncio.sleep(sleep_for)

        if keep_going is not None and not keep_going():
            return

        started = time.monotonic()
        try:
            if renew_timeout is not None:
                renewed = await asyncio.wait_for(renew(), max(0.0, renew_timeout()))
            else:
                renewed = await renew()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if retry_interval_seconds is None:
                raise
            remaining = 0.0
            if deadline is not None:
                remaining = deadline() - time.monotonic()
                if remaining <= retry_interval_seconds:
                    if on_transient_error is not None:
                        on_transient_error(exc, remaining)
                    on_lost()
                    return
            if on_transient_error is not None:
                on_transient_error(exc, remaining)
            delay = retry_interval_seconds
            continue

        if not renewed:
            on_lost()
            return
        if on_renewed is not None:
            on_renewed(started)
        delay = interval_seconds
