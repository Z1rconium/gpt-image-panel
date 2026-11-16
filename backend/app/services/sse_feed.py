"""Shared SSE connection lifecycle and keyed subscriber feed."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from ..core import settings as config
from ..core.errors import RateLimitedError
from ..core.observability import metrics
from ..repositories.sse_limiter import SSELease, SSELimiter, sse_limiter
from .job_events import publish_job_edges, publish_queue
from .poll_backoff import next_poll_delay

logger = logging.getLogger(__name__)

DEFAULT_KEEPALIVE_SECONDS = 15.0
DEFAULT_QUEUE_MAXSIZE = 20
KEEPALIVE_FRAME = ": keep-alive\n\n"

HeartbeatOutcome = Literal["continue", "keepalive", "stop"]


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


class SseConnection:
    """Lifecycle guard for one SSE HTTP stream (slot lease, refresh, TTL, keep-alive)."""

    KEEPALIVE_FRAME = KEEPALIVE_FRAME

    def __init__(
        self,
        *,
        client_ip: str,
        lease: SSELease,
        is_disconnected: Callable[[], Awaitable[bool]],
        keepalive_seconds: float,
        ttl_seconds: float | None,
        idle_ttl_only: bool,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[Any]],
        limiter: SSELimiter,
    ) -> None:
        self._client_ip = client_ip
        self._lease: SSELease | None = lease
        self._is_disconnected = is_disconnected
        self._keepalive_seconds = float(keepalive_seconds)
        self._ttl_seconds = ttl_seconds
        self._idle_ttl_only = idle_ttl_only
        self._clock = clock
        self._sleep = sleep
        self._limiter = limiter
        started = self._clock()
        self._started_at = started
        self._last_refresh_at = started
        self._last_sent_at = started
        self._last_activity_at = started
        self._closed = False

    @classmethod
    async def open(
        cls,
        client_ip: str,
        *,
        is_disconnected: Callable[[], Awaitable[bool]],
        keepalive_seconds: float = DEFAULT_KEEPALIVE_SECONDS,
        ttl_seconds: float | None = None,
        idle_ttl_only: bool = False,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
        limiter: SSELimiter = sse_limiter,
    ) -> SseConnection:
        """Acquire an SSE slot or raise ``RateLimitedError`` when exhausted."""
        lease = await limiter.acquire(client_ip)
        if not lease:
            raise RateLimitedError("Too many SSE connections")
        return cls(
            client_ip=client_ip,
            lease=lease,
            is_disconnected=is_disconnected,
            keepalive_seconds=keepalive_seconds,
            ttl_seconds=ttl_seconds,
            idle_ttl_only=idle_ttl_only,
            clock=clock or (lambda: time.monotonic()),
            sleep=sleep or asyncio.sleep,
            limiter=limiter,
        )

    def _ttl_limit(self) -> float:
        if self._ttl_seconds is not None:
            return float(self._ttl_seconds)
        return float(config.SSE_CONNECTION_TTL_SECONDS)

    def _ttl_anchor(self) -> float:
        return self._last_activity_at if self._idle_ttl_only else self._started_at

    def now(self) -> float:
        return self._clock()

    def mark_sent(self) -> float:
        """Record emission of a real SSE frame for keep-alive and idle TTL tracking."""
        current = self._clock()
        self._last_sent_at = current
        self._last_activity_at = current
        return current

    def wait_timeout(self, idle_check_seconds: float) -> float:
        """Compute the next bounded queue wait before keep-alive or TTL due."""
        current = self._clock()
        return min(
            idle_check_seconds,
            max(0.1, self._keepalive_seconds - (current - self._last_sent_at)),
            max(0.1, self._ttl_limit() - (current - self._ttl_anchor())),
        )

    async def sleep(self, seconds: float) -> None:
        await self._sleep(seconds)

    async def check_connection(self) -> bool:
        """Check client disconnect and refresh the SSE slot lease when due."""
        if self._closed or self._lease is None:
            return False
        if await self._is_disconnected():
            return False
        current = self._clock()
        refreshed_at = await self._limiter.refresh_if_needed(
            self._lease,
            self._last_refresh_at,
            now=current,
        )
        if refreshed_at is None:
            return False
        self._last_refresh_at = refreshed_at
        return True

    def check_idle(self) -> HeartbeatOutcome:
        """Evaluate TTL expiry and keep-alive due using the connection clock."""
        current = self._clock()
        if current - self._ttl_anchor() > self._ttl_limit():
            return "stop"
        if current - self._last_sent_at >= self._keepalive_seconds:
            self._last_sent_at = current
            return "keepalive"
        return "continue"

    async def heartbeat(self, *, idle: bool = True) -> HeartbeatOutcome:
        """Run disconnect, lease refresh, TTL, and keep-alive checks for one tick."""
        if not await self.check_connection():
            return "stop"
        if self._idle_ttl_only and not idle:
            return "continue"
        return self.check_idle()

    async def close(self) -> None:
        """Release the SSE slot idempotently, shielded from task cancellation."""
        if self._closed or self._lease is None:
            return
        self._closed = True
        lease = self._lease
        self._lease = None
        release_task = asyncio.ensure_future(self._limiter.release(lease))
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(release_task)
            except asyncio.CancelledError:
                release_task.add_done_callback(_consume_task_exception)
            raise

    async def __aenter__(self) -> SseConnection:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


class KeyedFeed:
    """Per-process keyed subscriber registry and cross-worker edge poller."""

    def __init__(
        self,
        *,
        name: str,
        get_keyed_subscribers: Callable[[], dict[str, set[asyncio.Queue[dict[str, Any]]]]],
        get_poller_task: Callable[[], asyncio.Task[Any] | None],
        set_poller_task: Callable[[asyncio.Task[Any] | None], None],
        read_edges: Callable[[set[str], bool], Awaitable[tuple[Any, dict[str, str]]]],
        read_event: Callable[[str], Awaitable[dict[str, Any]]],
        poll_metric_name: str,
        base_interval: Callable[[], float] | float,
        max_backoff: Callable[[], float] | float = lambda: config.SSE_IDLE_BACKOFF_MAX_SECONDS,
        get_list_subscribers: Callable[[], set[asyncio.Queue[dict[str, Any]]]] | None = None,
        read_list_event: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        default_queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
    ) -> None:
        self._name = name
        self._get_keyed_subscribers = get_keyed_subscribers
        self._get_poller_task = get_poller_task
        self._set_poller_task = set_poller_task
        self._read_edges = read_edges
        self._read_event = read_event
        self._poll_metric_name = poll_metric_name
        self._base_interval = base_interval
        self._max_backoff = max_backoff
        self._get_list_subscribers = get_list_subscribers
        self._read_list_event = read_list_event
        self._default_queue_maxsize = default_queue_maxsize

    @staticmethod
    def _resolve_interval(value: Callable[[], float] | float) -> float:
        return float(value() if callable(value) else value)

    def ensure_poller(self) -> None:
        task = self._get_poller_task()
        if task and not task.done():
            return
        self._set_poller_task(asyncio.create_task(self.run_poller()))

    @asynccontextmanager
    async def subscribe(
        self,
        key: str | None = None,
        *,
        maxsize: int | None = None,
        start_poller: bool = True,
    ) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Register a subscriber queue for ``key`` (or the list feed when ``None``)."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=self._default_queue_maxsize if maxsize is None else maxsize
        )
        if key is None:
            if self._get_list_subscribers is None:
                raise ValueError(f"{self._name} feed does not support unkeyed list subscriptions")
            list_subscribers = self._get_list_subscribers()
            list_subscribers.add(queue)
            keyed_subscribers = None
            subscribers = None
        else:
            list_subscribers = None
            keyed_subscribers = self._get_keyed_subscribers()
            subscribers = keyed_subscribers.setdefault(key, set())
            subscribers.add(queue)

        if start_poller:
            self.ensure_poller()

        try:
            yield queue
        finally:
            if list_subscribers is not None:
                list_subscribers.discard(queue)
            elif keyed_subscribers is not None and subscribers is not None and key is not None:
                subscribers.discard(queue)
                if not subscribers and keyed_subscribers.get(key) is subscribers:
                    keyed_subscribers.pop(key, None)

    def publish(self, key: str, event: dict[str, Any]) -> None:
        """Push an event immediately to every in-process subscriber for ``key``."""
        subscribers = self._get_keyed_subscribers().get(key, set())
        if not subscribers:
            return
        for queue in list(subscribers):
            publish_queue(queue, event)

    def publish_list(self, event: dict[str, Any]) -> None:
        """Push an event immediately to every in-process list-feed subscriber."""
        if self._get_list_subscribers is None:
            return
        for queue in list(self._get_list_subscribers()):
            publish_queue(queue, event)

    async def run_poller(
        self,
        *,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        """Poll edge timestamps and push cross-worker updates until no subscribers remain."""
        sleep_fn = sleep or asyncio.sleep
        last_list_edge: Any = None
        last_job_edges: dict[str, str] = {}
        delay = self._resolve_interval(self._base_interval)
        try:
            while True:
                list_subscribers = (
                    list(self._get_list_subscribers())
                    if self._get_list_subscribers is not None
                    else []
                )
                subscribers_by_job = {
                    key: list(subscribers)
                    for key, subscribers in self._get_keyed_subscribers().items()
                    if subscribers
                }
                if not list_subscribers and not subscribers_by_job:
                    break

                list_edge, job_edges = await self._read_edges(
                    set(subscribers_by_job),
                    bool(list_subscribers),
                )
                metrics.increment(self._poll_metric_name)
                changed = False

                if (
                    list_subscribers
                    and self._read_list_event is not None
                    and list_edge != last_list_edge
                ):
                    last_list_edge = list_edge
                    list_event = await self._read_list_event()
                    for queue in list_subscribers:
                        publish_queue(queue, list_event)
                    changed = True

                if subscribers_by_job:
                    changed = (
                        await publish_job_edges(
                            subscribers_by_job=subscribers_by_job,
                            edges=job_edges,
                            last_edges=last_job_edges,
                            read_event=self._read_event,
                        )
                        or changed
                    )
                else:
                    last_job_edges.clear()

                base = self._resolve_interval(self._base_interval)
                delay = next_poll_delay(
                    base_interval=base,
                    current_delay=delay,
                    changed=changed,
                    max_backoff_seconds=self._resolve_interval(self._max_backoff),
                )
                if delay > base:
                    metrics.increment("sse.poll_idle_backoff")
                await sleep_fn(delay)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("%s SSE poller stopped after error", self._name, exc_info=True)
        finally:
            if self._get_poller_task() is asyncio.current_task():
                self._set_poller_task(None)
