import asyncio
import json
import threading

from backend.tests.support.contract import *  # noqa: F403
from backend.app.integrations.nodeimage import client as nodeimage_client
from backend.app.integrations.r2 import config as r2_config
from backend.app.services import gallery_export_jobs, nodeimage_upload_jobs


def _parse_sse_events(body_text: str) -> list[tuple[str | None, dict]]:
    events: list[tuple[str | None, dict]] = []
    for block in body_text.split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        event_name: str | None = None
        data_raw: str | None = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_raw = line.split(":", 1)[1].strip()
        if data_raw is not None:
            events.append((event_name, json.loads(data_raw)))
    return events


def _enable_r2() -> None:
    settings_repo.save_r2_backup_settings(
        {
            "enabled": True,
            "endpoint_url": "https://account.r2.cloudflarestorage.com",
            "bucket_name": "image-backups",
            "region": "auto",
            "key_prefix": "gallery-test/",
            "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
            "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
        }
    )


def _enable_nodeimage() -> None:
    settings_repo.save_nodeimage_settings(
        {
            "enabled": True,
            "api_key": "${TEST_NODEIMAGE_API_KEY}",
        }
    )


def _wait_for_nodeimage_job(client, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/gallery/nodeimage-upload-jobs/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in {"success", "partial_failure", "cancelled", "error"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"nodeimage job {job_id} did not finish: {last}")


def test_gallery_export_kind_payload_and_terminal_states(client, monkeypatch):
    _fake_gallery_entry("char-exp-1", "one", "1024x1024", "char-exp-1.png")

    created = client.post("/api/gallery/export-jobs")
    assert created.status_code == 202
    initial = created.json()
    expected_keys = {
        "job_id",
        "status",
        "stage",
        "message",
        "progress",
        "filename",
        "download_url",
        "requested_count",
        "processed_count",
        "exported_count",
        "missing_count",
        "bytes_total",
        "bytes_written",
        "created_at",
        "updated_at",
        "error",
    }
    assert set(initial.keys()) == expected_keys
    assert initial["status"] == "queued"

    finished = _wait_for_gallery_export_job(client, initial["job_id"])
    assert set(finished.keys()) == expected_keys
    assert finished["status"] == "success"
    assert finished["stage"] == "ready"
    assert finished["progress"] == 100
    assert finished["requested_count"] == 1
    assert finished["processed_count"] == 1
    assert finished["exported_count"] == 1
    assert finished["missing_count"] == 0
    assert finished["error"] is None
    assert finished["download_url"] == f"/api/gallery/export-jobs/{initial['job_id']}/download"

    stored = coordination_repo.get_gallery_job("export", initial["job_id"])
    assert stored.get("lease_owner") is None
    assert stored.get("lease_expires_at") is None
    assert stored["completed_at"] is not None

    events_resp = client.get(f"/api/gallery/export-jobs/{initial['job_id']}/events")
    assert events_resp.status_code == 200
    events = _parse_sse_events(events_resp.text)
    assert len(events) == 1
    assert events[0][0] == "export"
    assert set(events[0][1].keys()) == expected_keys
    assert events[0][1]["status"] == "success"

    def fail_zip(*args, **kwargs):
        raise RuntimeError("zip write failed")

    monkeypatch.setattr(gallery_export_jobs, "write_gallery_zip_file", fail_zip)
    errored = client.post("/api/gallery/export-jobs")
    assert errored.status_code == 202
    err_finished = _wait_for_gallery_export_job(client, errored.json()["job_id"])
    assert set(err_finished.keys()) == expected_keys
    assert err_finished["status"] == "error"
    assert err_finished["stage"] == "error"
    assert err_finished["message"] == "Failed to build ZIP archive"
    assert err_finished["error"] == "zip write failed"
    err_stored = coordination_repo.get_gallery_job("export", errored.json()["job_id"])
    assert err_stored.get("lease_owner") is None
    assert err_stored.get("lease_expires_at") is None
    assert err_stored["completed_at"] is not None


def test_gallery_direct_export_kind_payload_and_terminal_states(client):
    _fake_gallery_entry("char-dexp-1", "one", "1024x1024", "char-dexp-1.png")

    created = client.post("/api/gallery/direct-export-jobs")
    assert created.status_code == 202
    job = created.json()
    archive = client.get(f"/api/download-all?export_job_id={job['job_id']}")
    assert archive.status_code == 200

    finished = _wait_for_gallery_direct_export_job(client, job["job_id"])
    assert finished["status"] == "success"
    assert finished["stage"] == "ready"
    assert finished["progress"] == 100
    assert finished["error"] is None
    stored = coordination_repo.get_gallery_job("export_direct", job["job_id"])
    assert stored.get("lease_owner") is None
    assert stored.get("lease_expires_at") is None
    assert stored["completed_at"] is not None

    events_resp = client.get(f"/api/gallery/direct-export-jobs/{job['job_id']}/events")
    assert events_resp.status_code == 200
    events = _parse_sse_events(events_resp.text)
    assert len(events) == 1
    assert events[0][0] == "export"
    assert events[0][1]["status"] == "success"

    # Also verify download_all_images error transition when gallery becomes empty.
    empty_created = client.post("/api/gallery/direct-export-jobs")
    assert empty_created.status_code == 202
    empty_job_id = empty_created.json()["job_id"]
    assert client.delete("/api/gallery").status_code == 200
    empty_download = client.get(f"/api/download-all?export_job_id={empty_job_id}")
    assert empty_download.status_code == 404
    empty_status = client.get(f"/api/gallery/direct-export-jobs/{empty_job_id}")
    assert empty_status.status_code == 200
    assert empty_status.json()["status"] == "error"
    assert empty_status.json()["stage"] == "error"
    assert empty_status.json()["error"] == "No images in gallery"
    empty_stored = coordination_repo.get_gallery_job("export_direct", empty_job_id)
    assert empty_stored.get("lease_owner") is None
    assert empty_stored.get("lease_expires_at") is None


def test_gallery_import_kind_payload_and_terminal_states(client):
    expected_keys = {
        "job_id",
        "status",
        "stage",
        "message",
        "progress",
        "requested_count",
        "processed_count",
        "imported_count",
        "skipped_count",
        "created_at",
        "updated_at",
        "error",
    }
    resp = _post_import_archive(client, _import_archive_bytes())
    assert resp.status_code == 202
    initial = resp.json()
    assert set(initial.keys()) == expected_keys
    assert initial["status"] == "queued"

    finished = _wait_for_gallery_import_job(client, initial["job_id"])
    assert set(finished.keys()) == expected_keys
    assert finished["status"] == "success"
    assert finished["stage"] == "completed"
    assert finished["progress"] == 100
    assert finished["requested_count"] == 1
    assert finished["processed_count"] == 1
    assert finished["imported_count"] == 1
    assert finished["skipped_count"] == 0
    assert finished["error"] is None

    stored = coordination_repo.get_gallery_job("import", initial["job_id"])
    assert stored.get("lease_owner") is None
    assert stored.get("lease_expires_at") is None
    assert stored["completed_at"] is not None

    events_resp = client.get(f"/api/gallery/import-jobs/{initial['job_id']}/events")
    assert events_resp.status_code == 200
    events = _parse_sse_events(events_resp.text)
    assert len(events) == 1
    assert events[0][0] == "import"
    assert set(events[0][1].keys()) == expected_keys
    assert events[0][1]["status"] == "success"

    bad_resp = _post_import_archive(
        client,
        _import_archive_bytes(image_name="images/fake.png", image_bytes=b"<svg></svg>"),
    )
    assert bad_resp.status_code == 202
    bad_finished = _wait_for_gallery_import_job(client, bad_resp.json()["job_id"])
    assert set(bad_finished.keys()) == expected_keys
    assert bad_finished["status"] == "error"
    assert bad_finished["stage"] == "error"
    assert bad_finished["progress"] == 100
    assert bad_finished["error"] == "No importable images found"
    bad_stored = coordination_repo.get_gallery_job("import", bad_resp.json()["job_id"])
    assert bad_stored.get("lease_owner") is None
    assert bad_stored.get("lease_expires_at") is None


def test_gallery_sync_kind_payload_and_terminal_states(client, monkeypatch):
    _fake_gallery_entry("char-sync-1", "one", "1024x1024", "char-sync-1.png")
    _enable_r2()
    expected_keys = {
        "job_id",
        "status",
        "stage",
        "message",
        "progress",
        "created_at",
        "updated_at",
        "error",
        "total_count",
        "compared_count",
        "uploaded_count",
        "pending_upload_count",
        "skipped_existing_count",
        "missing_local_count",
        "failed_count",
        "bytes_total",
        "bytes_uploaded",
        "dry_run",
        "checkpoint_filename",
    }

    def ok_sync(settings, entries, *, total_count, **_kwargs):
        list(entries)
        return r2_sync.R2SyncResult(total_count=total_count, compared_count=1, uploaded_count=1)

    monkeypatch.setattr(r2_sync, "sync_gallery_to_r2", ok_sync)
    created = client.post("/api/gallery/sync-jobs")
    assert created.status_code == 202
    initial = created.json()
    assert set(initial.keys()) == expected_keys
    assert initial["status"] == "queued"

    finished = _wait_for_gallery_sync_job(client, initial["job_id"])
    assert set(finished.keys()) == expected_keys
    assert finished["status"] == "success"
    assert finished["stage"] == "completed"
    assert finished["progress"] == 100
    assert finished["uploaded_count"] == 1
    assert finished["error"] is None

    stored = coordination_repo.get_gallery_job("sync", initial["job_id"])
    assert stored.get("lease_owner") is None
    assert stored.get("lease_expires_at") is None
    assert stored["completed_at"] is not None

    events_resp = client.get(f"/api/gallery/sync-jobs/{initial['job_id']}/events")
    assert events_resp.status_code == 200
    events = _parse_sse_events(events_resp.text)
    assert len(events) == 1
    assert events[0][0] == "sync"
    assert set(events[0][1].keys()) == expected_keys
    assert events[0][1]["status"] == "success"

    def partial_r2_err(*args, **kwargs):
        raise r2_config.R2SyncError(
            "upload failed",
            result=r2_sync.R2SyncResult(total_count=1, compared_count=1, failed_count=1),
        )

    monkeypatch.setattr(r2_sync, "sync_gallery_to_r2", partial_r2_err)
    err_created = client.post("/api/gallery/sync-jobs")
    assert err_created.status_code == 202
    err_finished = _wait_for_gallery_sync_job(client, err_created.json()["job_id"])
    assert err_finished["status"] == "error"
    assert err_finished["stage"] == "error"
    assert err_finished["progress"] == 100
    assert err_finished["failed_count"] == 1
    assert err_finished["error"] == "upload failed"
    err_stored = coordination_repo.get_gallery_job("sync", err_created.json()["job_id"])
    assert err_stored.get("lease_owner") is None
    assert err_stored.get("lease_expires_at") is None

    def crash_sync(*args, **kwargs):
        raise RuntimeError("unexpected sync failure")

    monkeypatch.setattr(r2_sync, "sync_gallery_to_r2", crash_sync)
    crash_created = client.post("/api/gallery/sync-jobs")
    assert crash_created.status_code == 202
    crash_finished = _wait_for_gallery_sync_job(client, crash_created.json()["job_id"])
    assert crash_finished["status"] == "error"
    assert crash_finished["stage"] == "error"
    assert crash_finished["error"] == "unexpected sync failure"
    crash_stored = coordination_repo.get_gallery_job("sync", crash_created.json()["job_id"])
    assert crash_stored.get("lease_owner") is None
    assert crash_stored.get("lease_expires_at") is None


def test_gallery_nodeimage_kind_payload_and_terminal_states(client, monkeypatch):
    _fake_gallery_entry("char-node-1", "one", "1024x1024", "char-node-1.png")
    _fake_gallery_entry("char-node-2", "two", "1024x1024", "char-node-2.png")
    _enable_nodeimage()
    expected_keys = {
        "job_id",
        "status",
        "stage",
        "message",
        "progress",
        "requested_count",
        "processed_count",
        "uploaded_count",
        "failed_count",
        "cancelled_count",
        "results",
        "created_at",
        "started_at",
        "completed_at",
        "updated_at",
        "error",
        "status_url",
        "events_url",
        "cancel_url",
    }

    async def ok_upload(_path, filename, _effective):
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(nodeimage_upload_jobs, "upload_image_file", ok_upload)
    created = client.post("/api/gallery/batch/nodeimage-upload", json={"ids": ["char-node-1"]})
    assert created.status_code == 202
    initial = created.json()
    assert set(initial.keys()) == expected_keys
    assert initial["status"] == "queued"

    finished = _wait_for_nodeimage_job(client, initial["job_id"])
    assert set(finished.keys()) == expected_keys
    assert finished["status"] == "success"
    assert finished["stage"] == "completed"
    assert finished["progress"] == 100
    assert finished["uploaded_count"] == 1
    assert finished["failed_count"] == 0
    assert finished["cancelled_count"] == 0
    assert finished["error"] is None

    stored = coordination_repo.get_gallery_job("nodeimage_upload", initial["job_id"])
    assert stored.get("lease_owner") is None
    assert stored.get("lease_expires_at") is None
    assert stored["completed_at"] is not None

    events_resp = client.get(f"/api/gallery/nodeimage-upload-jobs/{initial['job_id']}/events")
    assert events_resp.status_code == 200
    events = _parse_sse_events(events_resp.text)
    assert len(events) == 1
    assert events[0][0] == "nodeimage_upload"
    assert set(events[0][1].keys()) == expected_keys
    assert events[0][1]["status"] == "success"

    # Partial failure
    async def partial_upload(_path, filename, _effective):
        if filename == "char-node-2.png":
            raise nodeimage_client.NodeImageUploadError("upload error")
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(nodeimage_upload_jobs, "upload_image_file", partial_upload)
    partial_created = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"ids": ["char-node-1", "char-node-2"]},
    )
    assert partial_created.status_code == 202
    partial_finished = _wait_for_nodeimage_job(client, partial_created.json()["job_id"])
    assert partial_finished["status"] == "partial_failure"
    assert partial_finished["stage"] == "completed"
    assert partial_finished["uploaded_count"] == 1
    assert partial_finished["failed_count"] == 1

    # Cancellation while running
    started = threading.Event()
    release = threading.Event()

    async def slow_upload(_path, filename, _effective):
        started.set()
        await asyncio.to_thread(release.wait, 3)
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(config, "NODEIMAGE_UPLOAD_CONCURRENCY", 1)
    monkeypatch.setattr(nodeimage_upload_jobs, "upload_image_file", slow_upload)
    cancel_created = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"ids": ["char-node-1", "char-node-2"]},
    )
    assert cancel_created.status_code == 202
    assert started.wait(2)
    cancel_resp = client.post(f"/api/gallery/nodeimage-upload-jobs/{cancel_created.json()['job_id']}/cancel")
    assert cancel_resp.status_code == 200
    release.set()
    cancel_finished = _wait_for_nodeimage_job(client, cancel_created.json()["job_id"])
    assert cancel_finished["status"] == "cancelled"
    assert cancel_finished["stage"] == "cancelled"
    assert cancel_finished["cancelled_count"] == 1

    # Unexpected runner failure -> error
    monkeypatch.setattr(
        nodeimage_upload_jobs,
        "load_nodeimage_settings",
        lambda: (_ for _ in ()).throw(RuntimeError("settings broken")),
    )
    err_created = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"ids": ["char-node-1"]},
    )
    assert err_created.status_code == 202
    err_finished = _wait_for_nodeimage_job(client, err_created.json()["job_id"])
    assert err_finished["status"] == "error"
    assert err_finished["stage"] == "error"
    assert err_finished["error"] == "NodeImage upload job failed"
    err_stored = coordination_repo.get_gallery_job("nodeimage_upload", err_created.json()["job_id"])
    assert err_stored.get("lease_owner") is None
    assert err_stored.get("lease_expires_at") is None
