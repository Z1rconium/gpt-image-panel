"""Process-local runtime state shared by the api, services, and integrations layers.

Everything here is per-process: the FastAPI app binds its ``app.state`` to this
same object, so both spellings address one copy. Values a second worker process
must observe live in SQLite (leases, versions) instead - see repositories/.
"""

import asyncio
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

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


class AppState(State):
    """Typed process-local runtime state exposed on ``app.state``."""

    worker_id: str
    generate_jobs: dict[str, dict[str, Any]]
    generate_job_tasks: dict[str, asyncio.Task[Any]]
    generate_job_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]]
    generate_jobs_subscribers: set[asyncio.Queue[dict[str, Any]]]
    generate_jobs_broadcast_task: asyncio.Task[Any] | None
    generate_jobs_broadcast_reconcile: bool
    generate_jobs_sse_poller_task: asyncio.Task[Any] | None
    generate_job_last_persist_at: dict[str, float]
    generate_job_seen_at: dict[str, float]
    generate_job_preview_cache: dict[tuple[str, int, int], dict[str, Any]]
    webhook_delivery_tasks: set[asyncio.Task[Any]]
    image_queue_runtime_metrics: dict[str, int]
    runtime_coordination_metrics: dict[str, Any]
    runtime_resource_gauges: dict[str, int | float]
    event_loop_lag_last_ms: float
    latest_version_cache: dict[str, Any]
    latest_version_check_lock: asyncio.Lock
    image_unit_dispatcher_kick: asyncio.Event
    gallery_export_lock: asyncio.Lock
    gallery_job_subscribers: dict[str, dict[str, set[asyncio.Queue[dict[str, Any]]]]]
    gallery_job_sse_poller_tasks: dict[str, asyncio.Task[Any]]
    thumbnail_dispatcher_kick: asyncio.Event
    agent_turn_tasks: dict[str, asyncio.Task[Any]]
    agent_turn_reservations: int
    agent_turn_admissions: set[asyncio.Task[Any]]
    assistant_slot_cleanup_tasks: set[asyncio.Task[Any]]
    agent_turn_wakeups: dict[str, asyncio.Event]
    agent_turn_cancel_events: dict[str, asyncio.Event]
    agent_last_event_purge_at: float
    access_attempt_times: deque[float]


def utc_lease_expires_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))).isoformat()


def _apply_defaults(target: AppState) -> AppState:
    target.worker_id = f"{os.getpid()}-{id(target)}"
    target.generate_jobs = {}
    target.generate_job_tasks = {}
    target.generate_job_subscribers = {}
    target.generate_jobs_subscribers = set()
    target.generate_jobs_broadcast_task = None
    target.generate_jobs_broadcast_reconcile = False
    target.generate_jobs_sse_poller_task = None
    target.generate_job_last_persist_at = {}
    target.generate_job_seen_at = {}
    target.generate_job_preview_cache = {}
    target.webhook_delivery_tasks = set()
    target.image_queue_runtime_metrics = {
        "running": 0,
        "queued": 0,
        "pending_edit_source_bytes": 0,
    }
    target.runtime_coordination_metrics = {
        "gauges": {},
        "background_leases": [],
        "workers": [],
    }
    target.runtime_resource_gauges = {}
    target.event_loop_lag_last_ms = 0.0
    target.latest_version_cache = {}
    target.latest_version_check_lock = asyncio.Lock()
    target.image_unit_dispatcher_kick = asyncio.Event()
    target.gallery_export_lock = asyncio.Lock()
    target.gallery_job_subscribers = {kind: {} for kind in GALLERY_JOB_KINDS}
    target.gallery_job_sse_poller_tasks = {}
    target.thumbnail_dispatcher_kick = asyncio.Event()
    target.agent_turn_tasks = {}
    target.agent_turn_reservations = 0
    target.agent_turn_admissions = set()
    target.assistant_slot_cleanup_tasks = set()
    target.agent_turn_wakeups = {}
    target.agent_turn_cancel_events = {}
    target.agent_last_event_purge_at = 0.0
    # Rolling window of recent /api/access attempt timestamps (see
    # api/routers/access.py); bounded by ACCESS_GLOBAL_MAX_ATTEMPTS_PER_MINUTE.
    target.access_attempt_times = deque()
    return target


def create_app_state() -> AppState:
    """Create and populate a fresh typed process state container."""
    return _apply_defaults(AppState())


state = create_app_state()


def init_defaults() -> None:
    """Set the per-process defaults. The app lifespan calls this once on startup."""
    _apply_defaults(state)


def reset() -> None:
    """Drop everything the previous test left behind, then re-apply defaults."""
    state._state.clear()
    init_defaults()
