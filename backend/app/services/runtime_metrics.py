"""Low-frequency runtime snapshots and event-loop health sampling."""

import asyncio
import logging
import resource
import sys
import time

from ..runtime.state import state
from ..core import settings as config
from ..core.observability import build_metrics_snapshot, failure_rates, metrics
from ..repositories.coordination import refresh_runtime_coordination_metrics
from ..repositories.db import optimize_database_if_due
from ..repositories.image_jobs import get_image_queue_runtime_metrics
from ..runtime.blocking import executor_gauges, run_db_operation
from .job_queue import snapshot_queue_metrics


logger = logging.getLogger(__name__)


def _resource_gauges() -> dict[str, int | float]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss_scale = 1 if sys.platform == "darwin" else 1024
    return {
        "process.rss_peak_bytes": int(usage.ru_maxrss * rss_scale),
        "event_loop.lag_last_ms": float(
            getattr(state, "event_loop_lag_last_ms", 0.0)
        ),
    }


async def refresh_runtime_metrics_once(worker_id: str) -> None:
    queue_metrics = await run_db_operation(
        get_image_queue_runtime_metrics,
        metric_name="snapshot_image_queue",
    )
    state.image_queue_runtime_metrics = queue_metrics

    gauges = snapshot_queue_metrics()
    gauges.update(executor_gauges())
    gauges.update(_resource_gauges())
    local_snapshot = build_metrics_snapshot(gauges=gauges)
    local_snapshot["rates"] = failure_rates(local_snapshot["counters"])
    worker_payload = {
        "counters": local_snapshot["counters"],
        "gauges": local_snapshot["gauges"],
        "rates": local_snapshot["rates"],
        "timings_ms": local_snapshot["timings_ms"],
    }
    runtime = await run_db_operation(
        refresh_runtime_coordination_metrics,
        worker_id,
        worker_payload,
        metric_name="refresh_runtime_coordination_metrics",
        retry_busy=False,
    )
    state.runtime_coordination_metrics = runtime
    state.runtime_resource_gauges = _resource_gauges()

    from .job_events import reconcile_stale_generate_job_memory

    await reconcile_stale_generate_job_memory()

    # Refresh planner statistics on a slow cadence; the repository throttles it
    # to SQLITE_OPTIMIZE_INTERVAL_SECONDS so most cycles are a cheap no-op.
    if await run_db_operation(
        optimize_database_if_due,
        metric_name="optimize_database",
        retry_busy=False,
    ):
        metrics.increment("sqlite.optimize")


async def run_runtime_metrics_refresher(worker_id: str) -> None:
    while True:
        try:
            await refresh_runtime_metrics_once(worker_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            metrics.increment("runtime_metrics.refresh_failed")
            logger.warning("Runtime metrics refresh failed", exc_info=True)
        await asyncio.sleep(config.RUNTIME_METRICS_REFRESH_SECONDS)


async def run_event_loop_lag_observer() -> None:
    interval = config.EVENT_LOOP_LAG_SAMPLE_SECONDS
    expected = time.monotonic() + interval
    while True:
        await asyncio.sleep(interval)
        now = time.monotonic()
        lag_ms = max(0.0, (now - expected) * 1000)
        state.event_loop_lag_last_ms = lag_ms
        metrics.observe_ms("event_loop.lag", lag_ms)
        expected = now + interval


__all__ = [
    "refresh_runtime_metrics_once",
    "run_event_loop_lag_observer",
    "run_runtime_metrics_refresher",
]
