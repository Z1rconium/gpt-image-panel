import asyncio

from backend.tests.support.contract import *  # noqa: F403

from backend.app.core.utils import utc_now
from backend.app.services.generate_job_sse import generate_feed
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


def test_generate_sse_poller_backs_off_while_idle(tmp_path):
    _configure_runtime(tmp_path)
    now = utc_now()
    image_jobs_repo.upsert_generate_job(
        {
            "job_id": "job-x",
            "status": "running",
            "stage": "generating",
            "message": "Running",
            "n": 1,
            "created_at": now,
            "updated_at": now,
        }
    )

    base = config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS
    cap = config.SSE_IDLE_BACKOFF_MAX_SECONDS
    sleeps: list[float] = []

    async def scenario():
        subscription = generate_feed.subscribe("job-x", start_poller=False)
        queue = await subscription.__aenter__()

        async def fake_sleep(seconds: float):
            sleeps.append(seconds)
            if len(sleeps) >= 4:
                await subscription.__aexit__(None, None, None)

        await generate_feed.run_poller(sleep=fake_sleep)
        return queue.get_nowait()

    first_event = asyncio.run(scenario())

    assert first_event["event"] == "job"
    assert first_event["data"]["job_id"] == "job-x"
    # First cycle observes a new job edge, so it stays at the base cadence.
    assert sleeps[:3] == [base, base * 2, base * 4]
    assert sleeps[3] == cap
    assert sleeps == sorted(sleeps)
