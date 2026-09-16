"""NodeImage upload jobs: per-image uploads with a shared concurrency slot
and an auth probe that stops the batch on the first rejection."""

from .gallery_job_shared import (
    _gallery_job_lease_expires_at,
)

import asyncio
import logging
import os
from pathlib import Path

from .gallery_job_payloads import (
    _nodeimage_result_counts,
    _nodeimage_result_item,
)
from .gallery_job_sse import (
    _publish_gallery_job_sse,
)


from ..runtime.blocking import run_db_operation
from ..core.errors import RateLimitedError
from ..core import settings as config
from ..core.utils import utc_now
from ..repositories.coordination import (
    count_active_gallery_jobs,
    get_gallery_job,
    reserve_gallery_job_capacity,
    renew_gallery_job_lease,
    update_gallery_job,
)
from ..repositories.gallery.queries import get_gallery_entries_by_ids
from ..core.media import safe_image_path
from ..repositories.settings import load_nodeimage_settings
from ..schemas.gallery import GalleryEntry
from ..integrations.nodeimage.client import (
    NodeImageAuthError,
    NodeImageUploadError,
    NodeImageUploadResult,
    resolve_nodeimage_settings,
    upload_image_file,
)
from .gallery_common import (
    GALLERY_JOB_LEASE_SECONDS,
    MAX_ACTIVE_NODEIMAGE_UPLOAD_JOBS,
    NODEIMAGE_UPLOAD_JOB_KIND,
    NODEIMAGE_UPLOAD_TERMINAL_STATUSES,
)
logger = logging.getLogger(__name__)


NODEIMAGE_UPLOAD_SEMAPHORE = asyncio.Semaphore(
    max(1, int(getattr(config, "NODEIMAGE_UPLOAD_CONCURRENCY", 4) or 1))
)


_NODEIMAGE_UPLOAD_SEMAPHORE_LOOP: asyncio.AbstractEventLoop | None = None


_NODEIMAGE_UPLOAD_SEMAPHORE_CAPACITY = max(
    1,
    int(getattr(config, "NODEIMAGE_UPLOAD_CONCURRENCY", 4) or 1),
)


def _get_nodeimage_upload_semaphore() -> asyncio.Semaphore:
    global NODEIMAGE_UPLOAD_SEMAPHORE
    global _NODEIMAGE_UPLOAD_SEMAPHORE_CAPACITY, _NODEIMAGE_UPLOAD_SEMAPHORE_LOOP
    current_loop = asyncio.get_running_loop()
    capacity = max(1, int(getattr(config, "NODEIMAGE_UPLOAD_CONCURRENCY", 4) or 1))
    if (
        capacity != _NODEIMAGE_UPLOAD_SEMAPHORE_CAPACITY
        or _NODEIMAGE_UPLOAD_SEMAPHORE_LOOP is not current_loop
    ):
        NODEIMAGE_UPLOAD_SEMAPHORE = asyncio.Semaphore(capacity)
        _NODEIMAGE_UPLOAD_SEMAPHORE_CAPACITY = capacity
        _NODEIMAGE_UPLOAD_SEMAPHORE_LOOP = current_loop
    return NODEIMAGE_UPLOAD_SEMAPHORE


def _build_nodeimage_upload_job(
    ids: list[str],
    requested_count: int,
    missing_ids: list[str],
) -> dict:
    job_id = os.urandom(16).hex()
    now = utc_now()
    initial_results = [
        _nodeimage_result_item(
            image_id,
            None,
            "error",
            error="Gallery entry not found",
        )
        for image_id in missing_ids
    ]
    processed_count, _uploaded_count, failed_count, _cancelled_count = _nodeimage_result_counts(initial_results)
    return {
        "job_id": job_id,
        "kind": NODEIMAGE_UPLOAD_JOB_KIND,
        "status": "queued",
        "stage": "queued",
        "message": "Queued NodeImage upload",
        "progress": round((processed_count / max(1, requested_count)) * 100),
        "requested_count": requested_count,
        "processed_count": processed_count,
        "uploaded_count": 0,
        "failed_count": failed_count,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "payload": {
            "ids": list(ids),
            "results": initial_results,
            "cancel_requested": False,
        },
    }


async def _create_reserved_nodeimage_upload_job(
    ids: list[str],
    requested_count: int,
    missing_ids: list[str],
) -> dict:
    job = await asyncio.to_thread(
        reserve_gallery_job_capacity,
        job=_build_nodeimage_upload_job(ids, requested_count, missing_ids),
        counted_kinds=(NODEIMAGE_UPLOAD_JOB_KIND,),
        max_active=MAX_ACTIVE_NODEIMAGE_UPLOAD_JOBS,
    )
    if not job:
        active_count = await asyncio.to_thread(
            count_active_gallery_jobs,
            NODEIMAGE_UPLOAD_JOB_KIND,
        )
        raise RateLimitedError(f"Too many active NodeImage upload jobs ({active_count}). Please wait for the existing upload to complete.")
    return job


NodeImageFileInspection = Path | dict


def _inspect_nodeimage_file(entry: GalleryEntry) -> NodeImageFileInspection:
    path = safe_image_path(entry.filename)
    if not path:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file not found",
        )
    try:
        file_size = path.stat().st_size
    except FileNotFoundError:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file not found",
        )
    except OSError:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file could not be read",
        )
    if file_size > config.MAX_FILE_SIZE_MB * 1024 * 1024:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error=f"Image file is too large. Max size is {config.MAX_FILE_SIZE_MB} MB",
        )
    try:
        with path.open("rb"):
            pass
    except FileNotFoundError:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file not found",
        )
    except OSError:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file could not be read",
        )
    if file_size <= 0:
        return _nodeimage_result_item(
            entry.id,
            entry.filename,
            "error",
            error="Image file is empty",
        )
    return path


async def _nodeimage_job_cancel_requested(job_id: str) -> bool:
    current = await asyncio.to_thread(get_gallery_job, NODEIMAGE_UPLOAD_JOB_KIND, job_id)
    if not current:
        return True
    return bool((current.get("payload") or {}).get("cancel_requested"))


def _nodeimage_cancelled_item(entry: GalleryEntry) -> dict:
    return _nodeimage_result_item(
        entry.id,
        entry.filename,
        "cancelled",
        error="Cancelled before upload",
    )


async def _upload_nodeimage_entry(
    job_id: str,
    entry: GalleryEntry,
    effective,
    auth_failed: asyncio.Event,
    *,
    inspected_path: Path | None = None,
) -> dict:
    semaphore = _get_nodeimage_upload_semaphore()
    async with semaphore:
        if await _nodeimage_job_cancel_requested(job_id):
            return _nodeimage_cancelled_item(entry)

        if auth_failed.is_set():
            inspection = await asyncio.to_thread(_inspect_nodeimage_file, entry)
            if isinstance(inspection, dict):
                return inspection
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error="NodeImage API key was rejected.",
            )

        path = inspected_path
        if path is None:
            inspection = await asyncio.to_thread(_inspect_nodeimage_file, entry)
            if isinstance(inspection, dict):
                return inspection
            path = inspection
        if auth_failed.is_set():
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error="NodeImage API key was rejected.",
            )
        try:
            result: NodeImageUploadResult = await upload_image_file(
                path,
                path.name,
                effective,
            )
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "ok",
                url=result.url,
                markdown=result.markdown,
            )
        except NodeImageAuthError:
            auth_failed.set()
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error="NodeImage API key was rejected.",
            )
        except NodeImageUploadError as exc:
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error=str(exc) or "NodeImage upload failed.",
            )
        except FileNotFoundError:
            return _nodeimage_result_item(entry.id, entry.filename, "error", error="Image file not found")
        except OSError:
            return _nodeimage_result_item(entry.id, entry.filename, "error", error="Image file could not be read")
        except Exception as exc:
            logger.exception(
                "Unexpected NodeImage upload failure for gallery entry %s (%s)",
                entry.id,
                type(exc).__name__,
            )
            return _nodeimage_result_item(
                entry.id,
                entry.filename,
                "error",
                error="Unexpected upload failure",
            )


async def _publish_nodeimage_job(
    job_id: str,
    lease_owner: str,
    updates: dict,
) -> dict:
    job = await run_db_operation(
        update_gallery_job,
        job_id,
        updates,
        lease_owner=lease_owner,
        metric_name="update_nodeimage_upload_job",
    )
    if not job:
        raise _NodeImageLeaseLost()
    _publish_gallery_job_sse(job)
    return job


async def _run_nodeimage_upload_job(job: dict) -> None:
    job_id = str(job["job_id"])
    lease_owner = str(job.get("lease_owner") or "")
    stored_payload = job.get("payload") or {}
    ids = [str(value) for value in stored_payload.get("ids") or [] if str(value)]
    payload = dict(stored_payload)
    results_by_id: dict[str, dict] = {}
    for value in payload.get("results") or []:
        if not isinstance(value, dict):
            continue
        image_id = str(value.get("image_id") or "")
        if image_id and image_id not in results_by_id:
            results_by_id[image_id] = value
    payload["ids"] = ids
    requested_count = max(int(job.get("requested_count") or 0), len(ids))
    auth_failed = asyncio.Event()
    lease_lost = asyncio.Event()
    stop_lease_renewal = asyncio.Event()

    async def renew_lease() -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        stop_lease_renewal.wait(),
                        timeout=max(1.0, GALLERY_JOB_LEASE_SECONDS / 3),
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                renewed = await asyncio.to_thread(
                    renew_gallery_job_lease,
                    job_id=job_id,
                    lease_owner=lease_owner,
                    lease_expires_at=_gallery_job_lease_expires_at(),
                    now=utc_now(),
                )
                if not renewed:
                    lease_lost.set()
                    return
        except asyncio.CancelledError:
            raise

    renew_task = asyncio.create_task(renew_lease())

    def ordered_results() -> list[dict]:
        return [results_by_id[image_id] for image_id in ids if image_id in results_by_id]

    async def persist_results(*, status: str = "running", stage: str = "uploading", message: str = "Uploading images to NodeImage") -> None:
        results = ordered_results()
        processed_count, uploaded_count, failed_count, _cancelled_count = _nodeimage_result_counts(results)
        payload["results"] = results
        if lease_lost.is_set():
            raise _NodeImageLeaseLost()
        await _publish_nodeimage_job(
            job_id,
            lease_owner,
            {
                "status": status,
                "stage": stage,
                "message": message,
                "progress": 100 if status in NODEIMAGE_UPLOAD_TERMINAL_STATUSES else round(
                    (processed_count / max(1, requested_count)) * 100
                ),
                "requested_count": requested_count,
                "processed_count": processed_count,
                "uploaded_count": uploaded_count,
                "failed_count": failed_count,
                "payload": payload,
                "error": None,
            },
        )

    async def persist_item(item: dict) -> None:
        results_by_id[str(item["image_id"])] = item
        await persist_results()

    try:
        effective = resolve_nodeimage_settings(
            await asyncio.to_thread(load_nodeimage_settings)
        )
        entries = await asyncio.to_thread(get_gallery_entries_by_ids, ids)
        entries_by_id = {entry.id: entry for entry in entries}
        for image_id in ids:
            if image_id in results_by_id or image_id in entries_by_id:
                continue
            results_by_id[image_id] = _nodeimage_result_item(
                image_id,
                None,
                "error",
                error="Gallery entry not found",
            )

        pending_entries = [
            entries_by_id[image_id]
            for image_id in ids
            if image_id in entries_by_id and image_id not in results_by_id
        ]
        await persist_results(message="Preparing NodeImage upload")

        while pending_entries and not await _nodeimage_job_cancel_requested(job_id):
            probe_entry = pending_entries.pop(0)
            probe_inspection = await asyncio.to_thread(
                _inspect_nodeimage_file,
                probe_entry,
            )
            if isinstance(probe_inspection, dict):
                await persist_item(probe_inspection)
                continue
            await persist_item(
                await _upload_nodeimage_entry(
                    job_id,
                    probe_entry,
                    effective,
                    auth_failed,
                    inspected_path=probe_inspection,
                )
            )
            break

        if pending_entries and not await _nodeimage_job_cancel_requested(job_id):
            tasks = [
                asyncio.create_task(
                    _upload_nodeimage_entry(
                        job_id,
                        entry,
                        effective,
                        auth_failed,
                    )
                )
                for entry in pending_entries
            ]
            try:
                for task in asyncio.as_completed(tasks):
                    await persist_item(await task)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        if await _nodeimage_job_cancel_requested(job_id):
            for entry in pending_entries:
                if entry.id not in results_by_id:
                    results_by_id[entry.id] = _nodeimage_cancelled_item(entry)
        results = ordered_results()
        _processed_count, uploaded_count, failed_count, cancelled_count = _nodeimage_result_counts(results)
        if cancelled_count:
            terminal_status = "cancelled"
            terminal_message = "NodeImage upload cancelled"
        elif failed_count:
            terminal_status = "partial_failure"
            terminal_message = "NodeImage upload completed with failures"
        else:
            terminal_status = "success"
            terminal_message = "NodeImage upload complete"
        payload["results"] = results
        await _publish_nodeimage_job(
            job_id,
            lease_owner,
            {
                "status": terminal_status,
                "stage": "completed" if terminal_status != "cancelled" else "cancelled",
                "message": terminal_message,
                "progress": 100,
                "requested_count": requested_count,
                "processed_count": len(results),
                "uploaded_count": uploaded_count,
                "failed_count": failed_count,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                "payload": payload,
                "error": None,
            },
        )
    except _NodeImageLeaseLost:
        logger.info("NodeImage upload job %s stopped after losing its lease", job_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("NodeImage upload job %s failed", job_id)
        try:
            results = ordered_results()
            remaining_error = "NodeImage upload job failed"
            entries = await asyncio.to_thread(get_gallery_entries_by_ids, ids)
            for entry in entries:
                if entry.id not in results_by_id:
                    results_by_id[entry.id] = _nodeimage_result_item(
                        entry.id,
                        entry.filename,
                        "error",
                        error=remaining_error,
                    )
            payload["results"] = ordered_results()
            _processed_count, uploaded_count, failed_count, _cancelled_count = _nodeimage_result_counts(payload["results"])
            await _publish_nodeimage_job(
                job_id,
                lease_owner,
                {
                    "status": "error",
                    "stage": "error",
                    "message": "NodeImage upload failed",
                    "progress": 100,
                    "requested_count": requested_count,
                    "processed_count": len(payload["results"]),
                    "uploaded_count": uploaded_count,
                    "failed_count": failed_count,
                    "completed_at": utc_now(),
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "payload": payload,
                    "error": remaining_error,
                },
            )
        except _NodeImageLeaseLost:
            logger.info("NodeImage upload job %s stopped after losing its lease", job_id)
    finally:
        stop_lease_renewal.set()
        if not renew_task.done():
            renew_task.cancel()
        await asyncio.gather(renew_task, return_exceptions=True)
