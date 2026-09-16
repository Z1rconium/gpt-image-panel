import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ..core import secrets
from ..core import security as auth
from ..core import settings as config
from ..core import overall_config
from ..repositories.coordination import (
    acquire_background_lease,
    complete_background_lease,
    list_gallery_job_ids_with_files,
    release_background_lease,
)
from ..repositories.db import close_database_connections, verify_storage_writable
from ..repositories.gallery.mutations import (
    backfill_missing_gallery_bytes,
    sync_gallery_with_image_files,
)
from ..repositories.settings import sync_overall_config_env_values
from ..repositories.thumbnail_jobs import cleanup_auxiliary_state
from ..runtime.state import (
    STARTUP_MAINTENANCE_COMPLETED_TTL_SECONDS,
    STARTUP_MAINTENANCE_LEASE_SECONDS,
    init_defaults,
    state,
    utc_lease_expires_at,
)
from ..core.image_cost import configured_model_count


logger = logging.getLogger(__name__)
FRONTEND_BUILD_DIR = config.PROJECT_ROOT / "frontend" / "build"


def cleanup_stale_edit_source_files():
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


def cleanup_stale_gallery_export_files():
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    from . import presets
    from ..services import job_events, job_scheduler

    init_defaults()
    worker = state.worker_id
    secrets.configure_registry(config.SECRET_REGISTRY_JSON)
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.THUMBNAILS_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    rows = sync_overall_config_env_values(overall_config.current_env_snapshot())
    overall_config.apply_rows_to_config(
        rows,
        include_restart_required=True,
        overrides_only=True,
    )

    if not config.ACCESS_KEY and not config.ALLOW_UNAUTHENTICATED:
        raise RuntimeError(
            "ACCESS_KEY is required. Set ACCESS_KEY, or set "
            "ALLOW_UNAUTHENTICATED=true to explicitly run without authentication."
        )
    if (config.PUBLIC_IMAGE_BASE_URL or config.PUBLIC_THUMBNAIL_BASE_URL) and len(
        config.CDN_SIGNING_SECRET.encode("utf-8")
    ) < 32:
        raise RuntimeError(
            "Public CDN media URLs require CDN_SIGNING_SECRET with at least 32 bytes"
        )
    if config.ALLOW_UNAUTHENTICATED and not config.ACCESS_KEY:
        logger.error(
            "SECURITY: ALLOW_UNAUTHENTICATED=true and ACCESS_KEY is unset. All "
            "non-health API routes are running without authentication. Set "
            "ACCESS_KEY and restart, or keep this deployment isolated from "
            "untrusted networks."
        )
    auth.validate_proxy_config()

    startup_maintenance_owner = f"startup-maintenance:{worker}"
    run_startup_maintenance = await asyncio.to_thread(
        acquire_background_lease,
        name="startup_maintenance",
        owner=startup_maintenance_owner,
        lease_expires_at=utc_lease_expires_at(STARTUP_MAINTENANCE_LEASE_SECONDS),
        completed_ttl_seconds=STARTUP_MAINTENANCE_COMPLETED_TTL_SECONDS,
    )
    if not run_startup_maintenance:
        verify_storage_writable()
        logger.info("Skipping startup maintenance; another worker already owns it")
    else:
        try:
            cleanup_stale_edit_source_files()
            cleanup_stale_gallery_export_files()
            verify_storage_writable()
        except Exception:
            release_background_lease(
                name="startup_maintenance",
                owner=startup_maintenance_owner,
            )
            raise

    logger.info("Image jobs resume through SQLite unit leases")
    logger.info(
        "SQLite DB executor: workers=%s critical_busy_timeout_ms=%s "
        "critical_retry_attempts=%s slow_txn_warn_ms=%s",
        config.DB_EXECUTOR_WORKERS,
        config.SQLITE_CRITICAL_BUSY_TIMEOUT_MS,
        config.SQLITE_CRITICAL_BUSY_RETRY_ATTEMPTS,
        config.SQLITE_SLOW_TXN_WARN_MS,
    )
    logger.info(
        "Image cost estimation: %s model(s) have a configured pricing rate",
        configured_model_count(),
    )
    if run_startup_maintenance:
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
            release_background_lease(
                name="startup_maintenance",
                owner=startup_maintenance_owner,
            )
            raise

    async def _background_backfill_gallery_bytes():
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
                owner=startup_maintenance_owner,
            )

    state._backfill_task = (
        asyncio.create_task(_background_backfill_gallery_bytes())
        if run_startup_maintenance
        else None
    )
    presets.load_api_settings()
    presets.validate_configured_secret_bindings()
    state.image_unit_dispatcher_task = asyncio.create_task(
        job_scheduler.run_image_unit_dispatcher(worker)
    )
    from ..services import gallery_jobs, gallery_maintenance
    from ..services import assistant_batch
    state.thumbnail_dispatcher_task = asyncio.create_task(
        gallery_maintenance.run_thumbnail_dispatcher(worker)
    )
    state.gallery_export_dispatcher_task = asyncio.create_task(
        gallery_jobs.run_gallery_export_dispatcher(worker)
    )
    state.gallery_sync_dispatcher_task = asyncio.create_task(
        gallery_jobs.run_gallery_sync_dispatcher(worker)
    )
    state.gallery_import_dispatcher_task = asyncio.create_task(
        gallery_jobs.run_gallery_import_dispatcher(worker)
    )
    state.gallery_nodeimage_upload_dispatcher_task = asyncio.create_task(
        gallery_jobs.run_gallery_nodeimage_upload_dispatcher(worker)
    )
    state.gallery_export_gc_task = asyncio.create_task(
        gallery_maintenance.gc_gallery_export_jobs(worker)
    )
    state.gallery_file_gc_task = asyncio.create_task(
        gallery_maintenance.run_gallery_file_gc(worker)
    )
    state.gallery_r2_scheduled_sync_task = asyncio.create_task(
        gallery_maintenance.run_gallery_r2_scheduled_sync(worker)
    )
    state.gallery_ai_analyze_dispatcher_task = asyncio.create_task(
        assistant_batch.run_ai_analyze_dispatcher(worker)
    )
    job_events.reconcile_active_generate_jobs_from_storage()
    from ..services import runtime_metrics
    state.runtime_metrics_refresher_task = asyncio.create_task(
        runtime_metrics.run_runtime_metrics_refresher(worker)
    )
    state.event_loop_lag_observer_task = asyncio.create_task(
        runtime_metrics.run_event_loop_lag_observer()
    )
    try:
        yield
    finally:
        backfill_task = getattr(state, "_backfill_task", None)
        if backfill_task and not backfill_task.done():
            backfill_task.cancel()
        broadcast_task = getattr(state, "generate_jobs_broadcast_task", None)
        if broadcast_task and not broadcast_task.done():
            broadcast_task.cancel()
        generate_jobs_sse_poller_task = getattr(
            state,
            "generate_jobs_sse_poller_task",
            None,
        )
        if generate_jobs_sse_poller_task and not generate_jobs_sse_poller_task.done():
            generate_jobs_sse_poller_task.cancel()
        dispatcher_task = getattr(state, "image_unit_dispatcher_task", None)
        if dispatcher_task and not dispatcher_task.done():
            dispatcher_task.cancel()
        gallery_export_dispatcher_task = getattr(state, "gallery_export_dispatcher_task", None)
        if gallery_export_dispatcher_task and not gallery_export_dispatcher_task.done():
            gallery_export_dispatcher_task.cancel()
        gallery_sync_dispatcher_task = getattr(state, "gallery_sync_dispatcher_task", None)
        if gallery_sync_dispatcher_task and not gallery_sync_dispatcher_task.done():
            gallery_sync_dispatcher_task.cancel()
        gallery_import_dispatcher_task = getattr(state, "gallery_import_dispatcher_task", None)
        if gallery_import_dispatcher_task and not gallery_import_dispatcher_task.done():
            gallery_import_dispatcher_task.cancel()
        gallery_nodeimage_upload_dispatcher_task = getattr(
            state,
            "gallery_nodeimage_upload_dispatcher_task",
            None,
        )
        if gallery_nodeimage_upload_dispatcher_task and not gallery_nodeimage_upload_dispatcher_task.done():
            gallery_nodeimage_upload_dispatcher_task.cancel()
        thumbnail_dispatcher_task = getattr(state, "thumbnail_dispatcher_task", None)
        if thumbnail_dispatcher_task and not thumbnail_dispatcher_task.done():
            thumbnail_dispatcher_task.cancel()
        gc_task = getattr(state, "gallery_export_gc_task", None)
        if gc_task and not gc_task.done():
            gc_task.cancel()
        file_gc_task = getattr(state, "gallery_file_gc_task", None)
        if file_gc_task and not file_gc_task.done():
            file_gc_task.cancel()
        scheduled_sync_task = getattr(state, "gallery_r2_scheduled_sync_task", None)
        if scheduled_sync_task and not scheduled_sync_task.done():
            scheduled_sync_task.cancel()
        ai_analyze_dispatcher_task = getattr(state, "gallery_ai_analyze_dispatcher_task", None)
        if ai_analyze_dispatcher_task and not ai_analyze_dispatcher_task.done():
            ai_analyze_dispatcher_task.cancel()
        runtime_metrics_refresher_task = getattr(
            state, "runtime_metrics_refresher_task", None
        )
        if runtime_metrics_refresher_task and not runtime_metrics_refresher_task.done():
            runtime_metrics_refresher_task.cancel()
        event_loop_lag_observer_task = getattr(
            state, "event_loop_lag_observer_task", None
        )
        if event_loop_lag_observer_task and not event_loop_lag_observer_task.done():
            event_loop_lag_observer_task.cancel()
        gallery_job_sse_poller_tasks = list(
            getattr(state, "gallery_job_sse_poller_tasks", {}).values()
        )
        for task in gallery_job_sse_poller_tasks:
            if task and not task.done():
                task.cancel()
        webhook_delivery_tasks = list(
            getattr(state, "webhook_delivery_tasks", set())
        )
        for task in webhook_delivery_tasks:
            if task and not task.done():
                task.cancel()
        tasks = list(getattr(state, "generate_job_tasks", {}).values())
        for task in tasks:
            task.cancel()
        awaitables = [
            task
            for task in (
                backfill_task,
                broadcast_task,
                generate_jobs_sse_poller_task,
                dispatcher_task,
                thumbnail_dispatcher_task,
                gallery_export_dispatcher_task,
                gallery_sync_dispatcher_task,
                gallery_import_dispatcher_task,
                gallery_nodeimage_upload_dispatcher_task,
                gc_task,
                file_gc_task,
                scheduled_sync_task,
                ai_analyze_dispatcher_task,
                runtime_metrics_refresher_task,
                event_loop_lag_observer_task,
                *gallery_job_sse_poller_tasks,
                *webhook_delivery_tasks,
                *tasks,
            )
            if task
        ]
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)
        from ..integrations.session_pool import close_pool
        from ..runtime.blocking import close_blocking_executors
        await close_pool()
        await close_blocking_executors()
        close_database_connections()


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)
# Services read process state through runtime.state; the app binds the same
# object so app.state and runtime.state never diverge.
app.state = state
