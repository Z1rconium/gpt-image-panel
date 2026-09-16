"""Process-local runtime state shared by the api, services, and integrations layers.

Everything here is per-process: the FastAPI app binds its ``app.state`` to this
same object, so both spellings address one copy. Values a second worker process
must observe live in SQLite (leases, versions) instead - see repositories/.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

from starlette.datastructures import State

MAX_GENERATE_JOBS = 100
GENERATE_JOB_PERSIST_INTERVAL_SECONDS = 5.0
GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS = 0.35
STARTUP_MAINTENANCE_LEASE_SECONDS = 600
STARTUP_MAINTENANCE_COMPLETED_TTL_SECONDS = 600

GALLERY_JOB_KINDS = (
    "export",
    "export_direct",
    "sync",
    "import",
    "ai_analyze",
    "nodeimage_upload",
)

state = State()


def utc_lease_expires_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))).isoformat()


def init_defaults() -> None:
    """Set the per-process defaults. The app lifespan calls this once on startup."""
    state.worker_id = f"{os.getpid()}-{id(state)}"
    state.generate_jobs = {}
    state.generate_job_tasks = {}
    state.generate_job_subscribers = {}
    state.generate_jobs_subscribers = set()
    state.generate_jobs_broadcast_task = None
    state.generate_jobs_broadcast_reconcile = False
    state.generate_jobs_sse_poller_task = None
    state.generate_job_last_persist_at = {}
    state.webhook_delivery_tasks = set()
    state.image_queue_runtime_metrics = {
        "running": 0,
        "queued": 0,
        "pending_edit_source_bytes": 0,
    }
    state.runtime_coordination_metrics = {
        "gauges": {},
        "background_leases": [],
        "workers": [],
    }
    state.runtime_resource_gauges = {}
    state.event_loop_lag_last_ms = 0.0
    state.latest_version_cache = {}
    state.latest_version_check_lock = asyncio.Lock()
    state.image_unit_dispatcher_kick = asyncio.Event()
    state.gallery_export_lock = asyncio.Lock()
    state.gallery_job_subscribers = {kind: {} for kind in GALLERY_JOB_KINDS}
    state.gallery_job_sse_poller_tasks = {}
    state.thumbnail_dispatcher_kick = asyncio.Event()


def reset() -> None:
    """Drop everything the previous test left behind, then re-apply defaults."""
    state._state.clear()
    init_defaults()
