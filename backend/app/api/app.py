"""FastAPI application object and the per-process startup/shutdown sequence."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ..core import overall_config
from ..core import secrets
from ..core import security as auth
from ..core import settings as config
from ..core.image_cost import configured_model_count
from ..repositories.settings import sync_overall_config_env_values
from ..runtime.state import init_defaults, state
from ..services import dispatchers, job_events, presets, startup_maintenance


logger = logging.getLogger(__name__)
FRONTEND_BUILD_DIR = config.PROJECT_ROOT / "frontend" / "build"


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    state._backfill_task = await startup_maintenance.run()
    presets.load_api_settings()
    presets.validate_configured_secret_bindings()
    dispatchers.start()
    job_events.reconcile_active_generate_jobs_from_storage()
    try:
        yield
    finally:
        await dispatchers.shutdown()


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)
# Services read process state through runtime.state; the app binds the same
# object so app.state and runtime.state never diverge.
app.state = state
