"""Characterization tests for image-job terminal transitions (C7-S0)."""

from backend.tests.support.contract import *  # noqa: F403

from backend.app.core.errors import DomainError
from backend.app.core.utils import utc_now
from backend.app.services import image_job_aggregate, job_cancel, job_persistence
from backend.app.services.edit_source_types import EditImageSource, edit_source_to_payload


def _enqueue_edit_job(
    tmp_path: Path,
    *,
    job_id: str,
    filename: str = "source.png",
    webhook_url: str | None = None,
) -> tuple[Path, dict, list[dict]]:
    source_path = tmp_path / filename
    source_path.write_bytes(PNG_BYTES)
    source = EditImageSource(
        temp_path=source_path,
        byte_size=len(PNG_BYTES),
        filename=filename,
        content_type="image/png",
        role="image",
    )
    parent_job = {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "operation": "edit",
        "prompt": "edit characterization",
        "model": "gpt-image-2",
        "n": 1,
    }
    if webhook_url is not None:
        parent_job["webhook_url"] = webhook_url
    parent, units = image_jobs_repo.enqueue_image_job(
        parent_job=parent_job,
        operation="edit",
        request={"prompt": "edit characterization", "model": "gpt-image-2", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/edits",
        edit_sources=[edit_source_to_payload(source)],
        pending_edit_source_bytes=len(PNG_BYTES),
        max_active_generate_jobs=4,
        max_queued_generate_jobs=4,
        max_pending_edit_source_bytes=1024 * 1024,
    )
    return source_path, parent, units


def test_cancel_with_running_unit_releases_reservation_and_keeps_temp_files(tmp_path):
    _configure_runtime(tmp_path)
    source_path, _parent, units = _enqueue_edit_job(
        tmp_path, job_id="cancel-running-edit", filename="running-source.png"
    )
    claimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="token-running",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now=utc_now(),
        running_limit=4,
    )
    assert claimed is not None and claimed["unit_id"] == units[0]["unit_id"]
    assert image_jobs_repo.get_pending_edit_source_bytes() == len(PNG_BYTES)

    asyncio.run(job_cancel.cancel_image_job("cancel-running-edit"))

    parent = image_jobs_repo.get_generate_job("cancel-running-edit")
    assert parent is not None
    assert parent["status"] == "cancelled"
    assert image_jobs_repo.get_pending_edit_source_bytes() == 0
    assert source_path.exists() is True


def test_cancel_with_no_running_unit_deletes_temp_files_and_releases_reservation(tmp_path):
    _configure_runtime(tmp_path)
    source_path, _parent, _units = _enqueue_edit_job(
        tmp_path, job_id="cancel-queued-edit", filename="queued-source.png"
    )
    assert image_jobs_repo.get_pending_edit_source_bytes() == len(PNG_BYTES)
    assert source_path.exists() is True

    asyncio.run(job_cancel.cancel_image_job("cancel-queued-edit"))

    parent = image_jobs_repo.get_generate_job("cancel-queued-edit")
    assert parent is not None
    assert parent["status"] == "cancelled"
    assert image_jobs_repo.get_pending_edit_source_bytes() == 0
    assert source_path.exists() is False


def test_double_terminal_transition_dispatches_webhook_only_once(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    dispatch_calls: list[tuple[str, str]] = []

    async def tracking_dispatch(job):
        dispatch_calls.append((str(job["job_id"]), str(job.get("status") or "")))

    monkeypatch.setattr(job_persistence, "dispatch_job_webhook_async", tracking_dispatch)

    job_id = "double-terminal-job"
    image_jobs_repo.enqueue_image_job(
        parent_job={
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "operation": "generation",
            "prompt": "double terminal",
            "model": "gpt-image-2",
            "n": 1,
            "webhook_url": "https://example.com/webhook",
        },
        operation="generation",
        request={"prompt": "double terminal", "model": "gpt-image-2", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=4,
        max_queued_generate_jobs=4,
        max_pending_edit_source_bytes=1024 * 1024,
    )

    first = asyncio.run(
        job_persistence.store_generate_job_async(
            job_id,
            {
                "status": "success",
                "stage": "completed",
                "message": "Image generation completed",
                "completed_at": utc_now(),
            },
        )
    )
    second = asyncio.run(
        job_persistence.store_generate_job_async(
            job_id,
            {
                "status": "error",
                "stage": "generation_failed",
                "message": "late failure",
                "error": "late failure",
                "completed_at": utc_now(),
            },
        )
    )
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(job_cancel.cancel_image_job(job_id))

    assert exc_info.value.status_code == 409
    assert first["status"] == "success"
    assert second["status"] == "success"
    stored = image_jobs_repo.get_generate_job(job_id)
    assert stored is not None and stored["status"] == "success"
    assert dispatch_calls == [(job_id, "success")]


def test_finalize_race_second_finalize_observes_written_false_and_skips_webhook(
    tmp_path, monkeypatch
):
    _configure_runtime(tmp_path)
    dispatch_calls: list[str] = []

    async def tracking_dispatch(job):
        dispatch_calls.append(str(job["job_id"]))

    monkeypatch.setattr(job_persistence, "dispatch_job_webhook_async", tracking_dispatch)

    job_id = "finalize-race-job"
    image_jobs_repo.enqueue_image_job(
        parent_job={
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "operation": "generation",
            "prompt": "finalize race",
            "model": "gpt-image-2",
            "n": 1,
            "webhook_url": "https://example.com/webhook",
        },
        operation="generation",
        request={"prompt": "finalize race", "model": "gpt-image-2", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=4,
        max_queued_generate_jobs=4,
        max_pending_edit_source_bytes=1024 * 1024,
    )
    claimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        claim_token="token-finalize",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now=utc_now(),
        running_limit=4,
    )
    assert claimed is not None
    completed = image_jobs_repo.complete_image_job_unit(
        str(claimed["unit_id"]),
        claim_token="token-finalize",
        result={
            "images": [
                {
                    "image_id": "img-race-1",
                    "image_url": "/api/image/img-race-1.png",
                    "completed_at": utc_now(),
                }
            ]
        },
        stage_timings={},
        duration="0.50s",
        completed_at=utc_now(),
    )
    assert completed is not None

    first = asyncio.run(image_job_aggregate.aggregate_parent_image_job(job_id))
    assert first is not None and first["status"] == "success"
    assert dispatch_calls == [job_id]

    row, written = image_jobs_repo.finalize_parent_job_from_units(
        job_id,
        derive=lambda parent_row, aggregate_row: image_job_aggregate.derive_parent_update(
            parent_row, aggregate_row, operation="generation"
        ),
    )
    assert written is False
    assert row is not None and row["status"] == "success"

    second = asyncio.run(image_job_aggregate.aggregate_parent_image_job(job_id))
    assert second is not None and second["status"] == "success"
    assert dispatch_calls == [job_id]


def test_cancel_twice_dispatches_webhook_only_once(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    dispatch_calls: list[tuple[str, str]] = []

    async def tracking_dispatch(job):
        dispatch_calls.append((str(job["job_id"]), str(job.get("status") or "")))

    monkeypatch.setattr(job_persistence, "dispatch_job_webhook_async", tracking_dispatch)

    job_id = "cancel-twice-job"
    image_jobs_repo.enqueue_image_job(
        parent_job={
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "operation": "generation",
            "prompt": "cancel twice",
            "model": "gpt-image-2",
            "n": 1,
            "webhook_url": "https://example.com/webhook",
        },
        operation="generation",
        request={"prompt": "cancel twice", "model": "gpt-image-2", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=4,
        max_queued_generate_jobs=4,
        max_pending_edit_source_bytes=1024 * 1024,
    )

    asyncio.run(job_cancel.cancel_image_job(job_id))
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(job_cancel.cancel_image_job(job_id))

    assert exc_info.value.status_code == 409
    assert dispatch_calls == [(job_id, "cancelled")]

