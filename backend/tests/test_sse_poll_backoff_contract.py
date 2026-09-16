import asyncio

from backend.tests.support.contract import *  # noqa: F403

from backend.app.api.app import app
from backend.app.api.routers import generate as generate_router
from backend.app.services.poll_backoff import next_poll_delay


def test_next_poll_delay_doubles_while_idle_and_resets_on_change():
    base = 0.35
    cap = 2.0

    assert (
        next_poll_delay(
            base_interval=base,
            current_delay=base,
            changed=True,
            max_backoff_seconds=cap,
        )
        == base
    )

    delay = base
    delays: list[float] = []
    for _ in range(6):
        delay = next_poll_delay(
            base_interval=base,
            current_delay=delay,
            changed=False,
            max_backoff_seconds=cap,
        )
        delays.append(delay)

    assert delays[:4] == [0.7, 1.4, 2.0, 2.0]
    assert max(delays) == cap

    # A change snaps straight back to the base interval.
    assert (
        next_poll_delay(
            base_interval=base,
            current_delay=cap,
            changed=True,
            max_backoff_seconds=cap,
        )
        == base
    )

    # The cap never drops below the base interval.
    assert (
        next_poll_delay(
            base_interval=1.0,
            current_delay=1.0,
            changed=False,
            max_backoff_seconds=0.1,
        )
        == 1.0
    )


class _AsyncioStub:
    """Contain the sleep stub so the real asyncio module is left alone."""

    def __init__(self, sleep):
        self._sleep = sleep

    async def sleep(self, seconds):
        await self._sleep(seconds)

    def current_task(self):
        return asyncio.current_task()


def test_generate_sse_poller_backs_off_while_idle(monkeypatch):
    base = config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS
    cap = config.SSE_IDLE_BACKOFF_MAX_SECONDS
    sleeps: list[float] = []

    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        subscribers = generate_router.get_job_subscribers().setdefault("job-x", set())
        subscribers.add(queue)

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 4:
                subscribers.discard(queue)

        async def fake_db(callback, *args, **kwargs):
            if callback is generate_router.get_generate_sse_edges:
                return None, {"job-x": "t0"}
            return {"job_id": "job-x", "status": "running"}

        monkeypatch.setattr(generate_router, "asyncio", _AsyncioStub(fake_sleep))
        monkeypatch.setattr(generate_router, "run_db_operation", fake_db)
        await generate_router._poll_generate_sse()
        generate_router.get_job_subscribers().pop("job-x", None)

    app.state.generate_jobs_sse_poller_task = None
    asyncio.run(scenario())

    # First cycle observes a new job edge, so it stays at the base cadence.
    assert sleeps[:3] == [base, base * 2, base * 4]
    assert sleeps[3] == cap
    assert sleeps == sorted(sleeps)
