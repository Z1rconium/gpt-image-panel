"""One-shot startup maintenance, owned by a single worker through a background lease."""

import asyncio
import logging
from pathlib import Path

from ..core import settings as config
from ..repositories.coordination import (
    acquire_background_lease,
    complete_background_lease,
    list_gallery_job_ids_with_files,
    release_background_lease,
)
from ..repositories.db import verify_storage_writable
from ..repositories.gallery.mutations import (
    backfill_missing_gallery_bytes,
    sync_gallery_with_image_files,
)
from ..repositories.thumbnail_jobs import cleanup_auxiliary_state
from ..runtime.state import (
    STARTUP_MAINTENANCE_COMPLETED_TTL_SECONDS,
    STARTUP_MAINTENANCE_LEASE_SECONDS,
    state,
    utc_lease_expires_at,
)

logger = logging.getLogger(__name__)


def cleanup_stale_edit_source_files() -> None:
    temp_dir = Path(config.DATA_DIR) / "edit-sources"
    if not temp_dir.exists():
        return

    removed = 0
    for temp_path in temp_dir.glob("edit-source-*"):
        if not temp_path.is_file():
            continue
        try:
            temp_path.unlink()
            removed += 1
        except OSError:
            logger.warning("Failed to remove stale edit source temp file: %s", temp_path)
    if removed:
        logger.info("Removed %s stale edit source temp file(s)", removed)


def cleanup_stale_gallery_export_files() -> None:
    temp_dir = Path(config.DATA_DIR) / "exports"
    if not temp_dir.exists():
        return

    try:
        known_export_ids = list_gallery_job_ids_with_files("export")
    except Exception:
        logger.warning("Failed to load gallery export job records before temp cleanup", exc_info=True)
        return

    removed = 0
    for temp_path in temp_dir.glob("*.zip"):
        if not temp_path.is_file():
            continue
        if temp_path.stem in known_export_ids:
            continue
        try:
            temp_path.unlink()
            removed += 1
        except OSError:
            logger.warning("Failed to remove stale gallery export temp file: %s", temp_path)
    if removed:
        logger.info("Removed %s stale gallery export temp file(s)", removed)


async def _backfill_gallery_bytes(owner: str) -> None:
    await asyncio.sleep(1.0)
    try:
        updated = await asyncio.to_thread(backfill_missing_gallery_bytes)
        if updated:
            logger.info(
                "Backfilled byte sizes for %s legacy gallery entry record(s)",
                updated,
            )
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.warning("Failed to backfill legacy gallery byte sizes", exc_info=True)
    finally:
        await asyncio.to_thread(
            complete_background_lease,
            name="startup_maintenance",
            owner=owner,
        )


async def run() -> asyncio.Task | None:
    """Run the startup maintenance pass and return its background follow-up task."""
    owner = f"startup-maintenance:{state.worker_id}"
    acquired = await asyncio.to_thread(
        acquire_background_lease,
        name="startup_maintenance",
        owner=owner,
        lease_expires_at=utc_lease_expires_at(STARTUP_MAINTENANCE_LEASE_SECONDS),
        completed_ttl_seconds=STARTUP_MAINTENANCE_COMPLETED_TTL_SECONDS,
    )
    if not acquired:
        verify_storage_writable()
        logger.info("Skipping startup maintenance; another worker already owns it")
        return None

    try:
        cleanup_stale_edit_source_files()
        cleanup_stale_gallery_export_files()
        verify_storage_writable()
    except Exception:
        release_background_lease(name="startup_maintenance", owner=owner)
        raise

    try:
        removed_gallery_entries = sync_gallery_with_image_files()
        cleaned_auxiliary = cleanup_auxiliary_state()
        if removed_gallery_entries:
            logger.info(
                "Removed %s stale gallery entries for missing image files",
                removed_gallery_entries,
            )
        if any(cleaned_auxiliary.values()):
            logger.info("Cleaned stale auxiliary rows: %s", cleaned_auxiliary)
    except Exception:
        release_background_lease(name="startup_maintenance", owner=owner)
        raise

    return asyncio.create_task(_backfill_gallery_bytes(owner))
