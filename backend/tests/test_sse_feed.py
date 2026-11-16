"""Unit tests for the shared SSE connection lifecycle and slot shielding."""

import asyncio
import pytest

from backend.app.core.errors import RateLimitedError
from backend.app.repositories.sse_limiter import SSELimiter, sse_limiter
from backend.app.services.sse_feed import SseConnection
from backend.tests.support.contract import _configure_runtime


class _FakeClock:
    def __init__(self, initial: float = 100.0) -> None:
        self.now = initial

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def test_sse_connection_open_raises_when_slots_exhausted(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    from backend.app.core import settings as config

    monkeypatch.setattr(config, "MAX_SSE_SUBSCRIBERS_GLOBAL", 1)
    monkeypatch.setattr(config, "MAX_SSE_SUBSCRIBERS_PER_IP", 10)

    async def scenario():
        async def never_disconnected() -> bool:
            return False

        conn = await SseConnection.open("127.0.0.1", is_disconnected=never_disconnected)
        try:
            assert sse_limiter.global_count == 1
            with pytest.raises(RateLimitedError, match="Too many SSE connections"):
                await SseConnection.open("127.0.0.1", is_disconnected=never_disconnected)
        finally:
            await conn.close()
            # Idempotent second close must not fail or double-release.
            await conn.close()

    asyncio.run(scenario())
    assert sse_limiter.global_count == 0


def test_sse_connection_ttl_total_vs_idle_only(tmp_path):
    _configure_runtime(tmp_path)
    clock = _FakeClock(100.0)

    async def never_disconnected() -> bool:
        return False

    async def scenario():
        # Total-lifetime TTL expires regardless of mark_sent().
        total_conn = await SseConnection.open(
            "127.0.0.1",
            is_disconnected=never_disconnected,
            keepalive_seconds=15.0,
            ttl_seconds=20.0,
            idle_ttl_only=False,
            clock=clock.monotonic,
        )
        try:
            clock.advance(12.0)
            total_conn.mark_sent()
            clock.advance(10.0)  # total elapsed = 22 > 20, idle = 10
            assert await total_conn.heartbeat() == "stop"
        finally:
            await total_conn.close()

        # idle_ttl_only resets TTL anchor on mark_sent() but NOT on keep-alive.
        idle_conn = await SseConnection.open(
            "127.0.0.1",
            is_disconnected=never_disconnected,
            keepalive_seconds=15.0,
            ttl_seconds=20.0,
            idle_ttl_only=True,
            clock=clock.monotonic,
        )
        try:
            clock.advance(12.0)
            idle_conn.mark_sent()
            clock.advance(10.0)  # total = 22 > 20, idle since mark_sent = 10 <= 20
            assert await idle_conn.heartbeat() == "continue"
            clock.advance(6.0)   # idle = 16 >= 15 -> keep-alive (does NOT reset activity anchor)
            assert await idle_conn.heartbeat() == "keepalive"
            clock.advance(5.0)   # idle = 21 > 20 -> stop
            assert await idle_conn.heartbeat() == "stop"
        finally:
            await idle_conn.close()

    asyncio.run(scenario())
    assert sse_limiter.global_count == 0


def test_sse_connection_close_shields_slot_release_from_cancellation(tmp_path):
    _configure_runtime(tmp_path)
    limiter = SSELimiter()
    release_started = asyncio.Event()
    allow_release = asyncio.Event()
    original_release = limiter.release

    async def slow_release(lease):
        release_started.set()
        await allow_release.wait()
        await original_release(lease)

    limiter.release = slow_release  # type: ignore[method-assign]

    async def never_disconnected() -> bool:
        return False

    async def scenario():
        conn = await SseConnection.open(
            "127.0.0.1",
            is_disconnected=never_disconnected,
            limiter=limiter,
        )
        assert limiter.global_count == 1

        async def close_worker():
            await conn.close()

        task = asyncio.create_task(close_worker())
        await release_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        allow_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert limiter.global_count == 0
