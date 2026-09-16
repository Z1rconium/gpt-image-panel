"""Background dispatcher lifecycle: one set of tasks per worker process.

The app lifespan starts these once at startup and cancels them on shutdown;
keeping the registry here means the list is testable without booting FastAPI.
"""

import asyncio
import logging

from ..integrations.session_pool import close_pool
from ..repositories.db import close_database_connections
from ..runtime.blocking import close_blocking_executors
from ..runtime.state import state
from . import (
    assistant_batch,
    gallery_jobs,
    gallery_maintenance,
    job_scheduler,
    runtime_metrics,
)

logger = logging.getLogger(__name__)

DISPATCHER_TASKS = (
    (
        "image_unit_dispatcher_task",
        lambda worker: job_scheduler.run_image_unit_dispatcher(worker),
    ),
    (
        "thumbnail_dispatcher_task",
        lambda worker: gallery_maintenance.run_thumbnail_dispatcher(worker),
    ),
    (
        "gallery_export_dispatcher_task",
        lambda worker: gallery_jobs.run_gallery_export_dispatcher(worker),
    ),
    (
        "gallery_sync_dispatcher_task",
        lambda worker: gallery_jobs.run_gallery_sync_dispatcher(worker),
    ),
    (
        "gallery_import_dispatcher_task",
        lambda worker: gallery_jobs.run_gallery_import_dispatcher(worker),
    ),
    (
        "gallery_nodeimage_upload_dispatcher_task",
        lambda worker: gallery_jobs.run_gallery_nodeimage_upload_dispatcher(worker),
    ),
    (
        "gallery_export_gc_task",
        lambda worker: gallery_maintenance.gc_gallery_export_jobs(worker),
    ),
    (
        "gallery_file_gc_task",
        lambda worker: gallery_maintenance.run_gallery_file_gc(worker),
    ),
    (
        "gallery_r2_scheduled_sync_task",
        lambda worker: gallery_maintenance.run_gallery_r2_scheduled_sync(worker),
    ),
    (
        "gallery_ai_analyze_dispatcher_task",
        lambda worker: assistant_batch.run_ai_analyze_dispatcher(worker),
    ),
    (
        "runtime_metrics_refresher_task",
        lambda worker: runtime_metrics.run_runtime_metrics_refresher(worker),
    ),
    (
        "event_loop_lag_observer_task",
        lambda worker: runtime_metrics.run_event_loop_lag_observer(),
    ),
)


def start() -> None:
    worker = state.worker_id
    for attribute, runner in DISPATCHER_TASKS:
        setattr(state, attribute, asyncio.create_task(runner(worker)))


async def shutdown() -> None:
    pending: list[asyncio.Task] = []
    for attribute, _runner in DISPATCHER_TASKS:
        pending.append(getattr(state, attribute, None))
    pending.extend(
        (
            getattr(state, "_backfill_task", None),
            getattr(state, "generate_jobs_broadcast_task", None),
            getattr(state, "generate_jobs_sse_poller_task", None),
            *getattr(state, "gallery_job_sse_poller_tasks", {}).values(),
            *getattr(state, "webhook_delivery_tasks", set()),
            *getattr(state, "generate_job_tasks", {}).values(),
        )
    )
    tasks = [task for task in pending if task and not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    await close_pool()
    await close_blocking_executors()
    close_database_connections()
