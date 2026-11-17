import logging

from ..repositories.db.execution import run_db_operation
from . import (  # noqa: F401 - register per-kind runners in gallery_job_kinds
    gallery_export_jobs,
    gallery_import_jobs,
    gallery_sync_jobs,
    nodeimage_upload_jobs,
)
from .gallery_job_kinds import (
    get_gallery_job_kind,
    get_gallery_job_runner,
)
from .gallery_job_shared import (
    _gallery_job_lease_expires_at,
)


from ..core.observability import metrics
from ..core.utils import utc_now
from ..repositories.coordination import (
    claim_next_gallery_job,
    has_claimable_gallery_job,
)
from .claim_loop import fail_open_precheck, run_claim_loop
from .gallery_common import (
    GALLERY_JOB_DISPATCH_INTERVAL_SECONDS,
    GALLERY_JOB_DISPATCH_MAX_IDLE_BACKOFF_SECONDS,
    NODEIMAGE_UPLOAD_JOB_KIND,
)

logger = logging.getLogger(__name__)


async def _run_gallery_job_dispatcher(
    kind: str,
    worker_id: str,
    running_limit: int | None = None,
) -> None:
    descriptor = get_gallery_job_kind(kind)
    runner = get_gallery_job_runner(kind)
    effective_limit = running_limit if running_limit is not None else descriptor.running_limit
    counted_kinds = descriptor.counted_kinds or (kind,)

    async def claim_gallery_job():
        return await run_db_operation(claim_next_gallery_job,
            kind=kind,
            worker_id=worker_id,
            lease_expires_at=_gallery_job_lease_expires_at(),
            now=utc_now(),
            running_limit=effective_limit,
            counted_kinds=counted_kinds,
        )

    async def run_gallery_job(job: dict):
        await runner(job)

    async def has_claimable_gallery() -> bool:
        return await run_db_operation(has_claimable_gallery_job,
            kind=kind,
            now=utc_now(),
        )

    await run_claim_loop(
        claim_fn=claim_gallery_job,
        run_fn=run_gallery_job,
        running_limit=effective_limit,
        idle_interval=GALLERY_JOB_DISPATCH_INTERVAL_SECONDS,
        max_backoff=GALLERY_JOB_DISPATCH_MAX_IDLE_BACKOFF_SECONDS,
        claim_miss_fn=lambda: metrics.increment(f"gallery.{kind}.claim_miss"),
        claim_precheck_fn=fail_open_precheck(has_claimable_gallery),
        claim_precheck_metric=f"gallery.{kind}.claim_precheck_skipped",
        logger=logger,
        error_message=f"Gallery {kind} dispatcher error",
        task_name=f"gallery {kind} job",
    )


async def run_gallery_job_once(kind: str, job: dict) -> None:
    """Execute one claimed gallery job via the kind descriptor registry."""
    runner = get_gallery_job_runner(kind)
    await runner(job)


async def run_gallery_export_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("export", worker_id)


async def run_gallery_sync_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("sync", worker_id)


async def run_gallery_import_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("import", worker_id)


async def run_gallery_nodeimage_upload_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher(NODEIMAGE_UPLOAD_JOB_KIND, worker_id)


__all__ = [name for name in globals() if not name.startswith("__")]
