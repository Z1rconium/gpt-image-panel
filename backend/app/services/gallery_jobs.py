import asyncio
import logging

from .gallery_export_jobs import (
    _run_gallery_export_job,
)
from .gallery_import_jobs import (
    _run_gallery_import_job,
)
from .gallery_job_shared import (
    _claim_counted_gallery_kinds,
    _gallery_job_lease_expires_at,
)
from .gallery_sync_jobs import (
    _run_gallery_sync_job,
)
from .nodeimage_upload_jobs import (
    _run_nodeimage_upload_job,
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
    MAX_ACTIVE_EXPORT_JOBS,
    MAX_ACTIVE_IMPORT_JOBS,
    MAX_ACTIVE_NODEIMAGE_UPLOAD_JOBS,
    MAX_ACTIVE_SYNC_JOBS,
    NODEIMAGE_UPLOAD_JOB_KIND,
)

logger = logging.getLogger(__name__)


async def _run_gallery_job_dispatcher(kind: str, worker_id: str, running_limit: int) -> None:
    runner_by_kind = {
        "export": _run_gallery_export_job,
        "sync": _run_gallery_sync_job,
        "import": _run_gallery_import_job,
        NODEIMAGE_UPLOAD_JOB_KIND: _run_nodeimage_upload_job,
    }
    runner = runner_by_kind[kind]

    async def claim_gallery_job():
        return await asyncio.to_thread(
            claim_next_gallery_job,
            kind=kind,
            worker_id=worker_id,
            lease_expires_at=_gallery_job_lease_expires_at(),
            now=utc_now(),
            running_limit=running_limit,
            counted_kinds=_claim_counted_gallery_kinds(kind),
        )

    async def run_gallery_job(job: dict):
        await runner(job)

    async def has_claimable_gallery() -> bool:
        return await asyncio.to_thread(
            has_claimable_gallery_job,
            kind=kind,
            now=utc_now(),
        )

    await run_claim_loop(
        claim_fn=claim_gallery_job,
        run_fn=run_gallery_job,
        running_limit=running_limit,
        idle_interval=GALLERY_JOB_DISPATCH_INTERVAL_SECONDS,
        max_backoff=GALLERY_JOB_DISPATCH_MAX_IDLE_BACKOFF_SECONDS,
        claim_miss_fn=lambda: metrics.increment(f"gallery.{kind}.claim_miss"),
        claim_precheck_fn=fail_open_precheck(has_claimable_gallery),
        claim_precheck_metric=f"gallery.{kind}.claim_precheck_skipped",
        logger=logger,
        error_message=f"Gallery {kind} dispatcher error",
        task_name=f"gallery {kind} job",
    )


async def run_gallery_export_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("export", worker_id, MAX_ACTIVE_EXPORT_JOBS)


async def run_gallery_sync_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("sync", worker_id, MAX_ACTIVE_SYNC_JOBS)


async def run_gallery_import_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("import", worker_id, MAX_ACTIVE_IMPORT_JOBS)


async def run_gallery_nodeimage_upload_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher(
        NODEIMAGE_UPLOAD_JOB_KIND,
        worker_id,
        MAX_ACTIVE_NODEIMAGE_UPLOAD_JOBS,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
