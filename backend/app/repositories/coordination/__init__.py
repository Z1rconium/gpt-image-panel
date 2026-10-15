"""Stable operations for SQLite-backed coordination persistence.

Implementations are owned by access, gallery_jobs, sse_slots, leases, and workers.
New repository code should import from those owners directly.
"""

from .access import (
    get_access_lockout,
    record_access_failure,
    clear_access_failure,
    list_access_failures,
)

from .gallery_jobs import (
    create_gallery_job,
    get_gallery_job,
    get_gallery_jobs_updated_at_edges,
    update_gallery_job,
    update_gallery_job_progress,
    request_gallery_job_cancellation,
    renew_gallery_job_lease,
    count_active_gallery_jobs,
    reserve_import_upload_capacity,
    resize_import_upload_reservation,
    release_import_upload_reservation,
    reserve_gallery_job_capacity,
    has_claimable_gallery_job,
    claim_next_gallery_job,
    cleanup_expired_gallery_jobs,
    cleanup_stale_gallery_jobs,
    delete_gallery_job,
    list_gallery_job_ids_with_files,
)

from .sse_slots import (
    acquire_sse_slot,
    refresh_sse_slot,
    release_sse_slot,
    count_active_sse_slots,
)

from .leases import (
    acquire_background_lease,
    complete_background_lease,
    release_background_lease,
    acquire_background_slot,
    renew_background_slot,
    release_background_slot,
)

from .workers import (
    mark_worker_heartbeat,
    get_runtime_coordination_metrics,
    refresh_runtime_coordination_metrics,
)

__all__ = [
    "get_access_lockout",
    "record_access_failure",
    "clear_access_failure",
    "list_access_failures",
    "create_gallery_job",
    "get_gallery_job",
    "get_gallery_jobs_updated_at_edges",
    "update_gallery_job",
    "update_gallery_job_progress",
    "request_gallery_job_cancellation",
    "renew_gallery_job_lease",
    "count_active_gallery_jobs",
    "reserve_import_upload_capacity",
    "resize_import_upload_reservation",
    "release_import_upload_reservation",
    "reserve_gallery_job_capacity",
    "has_claimable_gallery_job",
    "claim_next_gallery_job",
    "cleanup_expired_gallery_jobs",
    "cleanup_stale_gallery_jobs",
    "delete_gallery_job",
    "list_gallery_job_ids_with_files",
    "acquire_sse_slot",
    "refresh_sse_slot",
    "release_sse_slot",
    "count_active_sse_slots",
    "acquire_background_lease",
    "complete_background_lease",
    "release_background_lease",
    "acquire_background_slot",
    "renew_background_slot",
    "release_background_slot",
    "mark_worker_heartbeat",
    "get_runtime_coordination_metrics",
    "refresh_runtime_coordination_metrics",
]
