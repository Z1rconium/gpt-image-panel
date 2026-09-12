"""SQLite image-unit dispatcher and worker heartbeat loop."""

import asyncio
import logging
import time
import uuid

from ..core import settings as config
from ..core.observability import metrics
from ..core.utils import utc_now
from ..repositories.coordination import mark_worker_heartbeat
from ..repositories.image_jobs import (
    claim_next_image_job_unit,
    expire_exhausted_image_job_units,
)
from .claim_loop import run_claim_loop
from .job_executor import (
    aggregate_parent_image_job,
    image_unit_lease_expires_at,
    run_claimed_image_unit,
)
from .job_queue import get_image_unit_dispatcher_kick_event
from .blocking import run_db_operation

logger = logging.getLogger(__name__)

IMAGE_DISPATCHER_HEARTBEAT_INTERVAL_SECONDS = 5.0
IMAGE_DISPATCHER_MAX_IDLE_BACKOFF_SECONDS = 2.0


async def run_image_unit_dispatcher(worker_id: str):
    logger.info("Image unit dispatcher started: worker_id=%s", worker_id)
    last_heartbeat_at = 0.0
    last_heartbeat_active_units: int | None = None
    last_exhausted_check_at = 0.0

    async def heartbeat_if_needed(
        active_tasks: set[asyncio.Task],
        *,
        force: bool = False,
    ):
        nonlocal last_heartbeat_active_units, last_heartbeat_at
        active_units = len(active_tasks)
        now = time.monotonic()
        if (
            force
            or active_units != last_heartbeat_active_units
            or now - last_heartbeat_at >= IMAGE_DISPATCHER_HEARTBEAT_INTERVAL_SECONDS
        ):
            await run_db_operation(
                mark_worker_heartbeat,
                worker_id,
                active_units,
                metric_name="worker_heartbeat",
            )
            last_heartbeat_active_units = active_units
            last_heartbeat_at = now

    async def expire_exhausted_units_if_needed():
        nonlocal last_exhausted_check_at
        now = time.monotonic()
        interval = max(5.0, config.IMAGE_JOB_UNIT_LEASE_SECONDS / 2)
        if now - last_exhausted_check_at < interval:
            return
        last_exhausted_check_at = now
        expired = await run_db_operation(
            expire_exhausted_image_job_units,
            utc_now(),
            config.IMAGE_JOB_UNIT_MAX_ATTEMPTS,
            metric_name="expire_exhausted_image_job_units",
        )
        for unit in expired:
            parent_job_id = str(unit.get("parent_job_id") or "")
            if not parent_job_id:
                continue
            logger.warning(
                "Image unit exhausted its attempts and was interrupted: "
                "unit_id=%s parent_job_id=%s worker_id=%s",
                unit.get("unit_id"),
                parent_job_id,
                worker_id,
            )
            await aggregate_parent_image_job(parent_job_id, force_publish=True)

    async def claim_unit():
        return await run_db_operation(
            claim_next_image_job_unit,
            worker_id=worker_id,
            claim_token=str(uuid.uuid4().hex),
            lease_expires_at=image_unit_lease_expires_at(),
            now=utc_now(),
            running_limit=config.MAX_ACTIVE_GENERATE_JOBS,
            max_attempts=config.IMAGE_JOB_UNIT_MAX_ATTEMPTS,
            metric_name="claim_image_job_unit",
        )

    async def run_unit(unit: dict):
        await run_claimed_image_unit(unit, worker_id)

    async def before_cycle(active_tasks: set[asyncio.Task]):
        await heartbeat_if_needed(active_tasks)
        await expire_exhausted_units_if_needed()

    async def after_claims(active_tasks: set[asyncio.Task], claimed_count: int):
        if claimed_count > 0:
            await heartbeat_if_needed(active_tasks, force=True)

    def sleep_interval(active_tasks: set[asyncio.Task], idle_delay: float) -> float:
        if not active_tasks:
            return idle_delay
        next_heartbeat_in = max(
            0.0,
            IMAGE_DISPATCHER_HEARTBEAT_INTERVAL_SECONDS
            - (time.monotonic() - last_heartbeat_at),
        )
        return min(idle_delay, next_heartbeat_in or idle_delay)

    await run_claim_loop(
        claim_fn=claim_unit,
        run_fn=run_unit,
        running_limit=lambda: config.MAX_ACTIVE_GENERATE_JOBS,
        idle_interval=lambda: config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS,
        max_backoff=IMAGE_DISPATCHER_MAX_IDLE_BACKOFF_SECONDS,
        kick_event=get_image_unit_dispatcher_kick_event(),
        claim_miss_fn=lambda: metrics.increment("image_jobs.claim_miss"),
        before_cycle=before_cycle,
        after_claims=after_claims,
        sleep_interval_fn=sleep_interval,
        logger=logger,
        error_message="Image unit dispatcher error",
        task_name="image unit",
    )
