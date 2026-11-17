"""Descriptor table for gallery background job kinds.

Centralizes per-kind metadata (SSE event names, terminal statuses, payload
builders, capacity limits, TTLs, not-found messages, and runner registration)
without importing web frameworks or runner implementations at module load."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .gallery_common import (
    AI_ANALYZE_JOB_KIND,
    AI_ANALYZE_JOB_TTL_SECONDS,
    EXPORT_JOB_TTL_SECONDS,
    GALLERY_EXPORT_TERMINAL_STATUSES,
    GALLERY_IMPORT_TERMINAL_STATUSES,
    GALLERY_SYNC_TERMINAL_STATUSES,
    IMPORT_JOB_TTL_SECONDS,
    MAX_ACTIVE_EXPORT_JOBS,
    MAX_ACTIVE_IMPORT_JOBS,
    MAX_ACTIVE_NODEIMAGE_UPLOAD_JOBS,
    MAX_ACTIVE_SYNC_JOBS,
    NODEIMAGE_UPLOAD_JOB_KIND,
    NODEIMAGE_UPLOAD_JOB_TTL_SECONDS,
    NODEIMAGE_UPLOAD_TERMINAL_STATUSES,
    SYNC_JOB_TTL_SECONDS,
)
from .gallery_job_payloads import (
    _gallery_ai_analyze_payload,
    _gallery_export_payload,
    _gallery_import_payload,
    _gallery_sync_payload,
    _nodeimage_upload_payload,
)

GalleryPayloadBuilder = Callable[[dict[str, Any]], dict[str, Any]]
GalleryJobRunner = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class GalleryJobKind:
    """Lifecycle descriptor for one gallery background job kind."""

    kind: str
    event_name: str
    terminal_statuses: frozenset[str]
    payload: GalleryPayloadBuilder
    not_found_detail: str
    running_limit: int = 1
    counted_kinds: tuple[str, ...] = ()
    ttl_seconds: int = 1800
    runner: GalleryJobRunner | None = None


_KIND_DESCRIPTORS: tuple[GalleryJobKind, ...] = (
    GalleryJobKind(
        kind="export",
        event_name="export",
        terminal_statuses=frozenset(GALLERY_EXPORT_TERMINAL_STATUSES),
        payload=_gallery_export_payload,
        not_found_detail="Gallery export job not found",
        running_limit=MAX_ACTIVE_EXPORT_JOBS,
        counted_kinds=("export", "export_direct"),
        ttl_seconds=EXPORT_JOB_TTL_SECONDS,
    ),
    GalleryJobKind(
        kind="export_direct",
        event_name="export",
        terminal_statuses=frozenset(GALLERY_EXPORT_TERMINAL_STATUSES),
        payload=_gallery_export_payload,
        not_found_detail="Direct gallery export job not found",
        running_limit=MAX_ACTIVE_EXPORT_JOBS,
        counted_kinds=("export", "export_direct"),
        ttl_seconds=EXPORT_JOB_TTL_SECONDS,
    ),
    GalleryJobKind(
        kind="sync",
        event_name="sync",
        terminal_statuses=frozenset(GALLERY_SYNC_TERMINAL_STATUSES),
        payload=_gallery_sync_payload,
        not_found_detail="Gallery sync job not found",
        running_limit=MAX_ACTIVE_SYNC_JOBS,
        counted_kinds=("sync",),
        ttl_seconds=SYNC_JOB_TTL_SECONDS,
    ),
    GalleryJobKind(
        kind="import",
        event_name="import",
        terminal_statuses=frozenset(GALLERY_IMPORT_TERMINAL_STATUSES),
        payload=_gallery_import_payload,
        not_found_detail="Gallery import job not found",
        running_limit=MAX_ACTIVE_IMPORT_JOBS,
        counted_kinds=("import",),
        ttl_seconds=IMPORT_JOB_TTL_SECONDS,
    ),
    GalleryJobKind(
        kind=NODEIMAGE_UPLOAD_JOB_KIND,
        event_name="nodeimage_upload",
        terminal_statuses=frozenset(NODEIMAGE_UPLOAD_TERMINAL_STATUSES),
        payload=_nodeimage_upload_payload,
        not_found_detail="NodeImage upload job not found",
        running_limit=MAX_ACTIVE_NODEIMAGE_UPLOAD_JOBS,
        counted_kinds=(NODEIMAGE_UPLOAD_JOB_KIND,),
        ttl_seconds=NODEIMAGE_UPLOAD_JOB_TTL_SECONDS,
    ),
    GalleryJobKind(
        kind=AI_ANALYZE_JOB_KIND,
        event_name="analysis",
        terminal_statuses=frozenset({"success", "error"}),
        payload=_gallery_ai_analyze_payload,
        not_found_detail="Gallery AI analysis job not found",
        running_limit=1,
        counted_kinds=(AI_ANALYZE_JOB_KIND,),
        ttl_seconds=AI_ANALYZE_JOB_TTL_SECONDS,
    ),
)

GALLERY_JOB_KINDS: dict[str, GalleryJobKind] = {
    descriptor.kind: descriptor for descriptor in _KIND_DESCRIPTORS
}


def get_gallery_job_kind(kind: str) -> GalleryJobKind:
    """Return the registered descriptor for ``kind`` or raise ``KeyError``."""
    descriptor = GALLERY_JOB_KINDS.get(kind)
    if descriptor is None:
        raise KeyError(f"Unknown gallery job kind: {kind!r}")
    return descriptor


def register_gallery_job_runner(kind: str, runner: GalleryJobRunner) -> GalleryJobRunner:
    """Attach the async runner function to an existing ``GalleryJobKind`` descriptor."""
    get_gallery_job_kind(kind).runner = runner
    return runner


def get_gallery_job_runner(kind: str) -> GalleryJobRunner:
    """Return the registered runner for ``kind`` or raise ``RuntimeError``."""
    runner = get_gallery_job_kind(kind).runner
    if runner is None:
        raise RuntimeError(f"No runner registered for gallery job kind {kind!r}")
    return runner


def gallery_job_event_name(kind: str) -> str:
    return get_gallery_job_kind(kind).event_name


def gallery_job_payload(kind: str, job: dict[str, Any]) -> dict[str, Any]:
    return get_gallery_job_kind(kind).payload(job)
