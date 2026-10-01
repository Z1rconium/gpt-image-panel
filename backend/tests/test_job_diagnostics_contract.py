"""Job diagnostics endpoint contract tests.

The endpoint returns only the redacted phase summary and boolean URL markers;
raw follow-up URLs, credentials and query strings must never leave the API.
"""

import json
import uuid

from backend.tests.support.contract import *  # noqa: F403


def test_job_diagnostics_endpoint_returns_redacted_unit_payload(client):
    job_id = f"diag-{uuid.uuid4().hex}"
    parent, units = image_jobs_repo.enqueue_image_job(  # noqa: F405
        parent_job={"job_id": job_id, "status": "queued"},
        operation="generation",
        request={"prompt": "diag", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=4,
        max_queued_generate_jobs=4,
        max_pending_edit_source_bytes=1024 * 1024,
    )
    unit_id = str(units[0]["unit_id"])
    payload = {
        "code": "task_id_missing",
        "stages": [
            {
                "phase": "submit",
                "http": {"method": "POST", "url": "https://api.example.com/submit"},
                "snapshot": {"authorization": "[REDACTED]"},
            }
        ],
    }
    with db_repo._connect() as conn:  # noqa: F405
        with db_repo._transaction(conn):  # noqa: F405
            conn.execute(
                "UPDATE image_job_units SET diagnostics_json = ?, remote_json = ? WHERE unit_id = ?",
                (
                    json.dumps(payload),
                    json.dumps(
                        {
                            "phase": "submitted",
                            "task_id": "job-9",
                            "status_url": "https://api.example.com/jobs/9?token=secret",
                            "deadline_at": "2026-01-01T00:10:00+00:00",
                        }
                    ),
                    unit_id,
                ),
            )

    response = client.get(f"/api/generate/{job_id}/diagnostics")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == job_id
    assert len(body["units"]) == 1
    unit = body["units"][0]
    assert unit["diagnostics"]["code"] == "task_id_missing"
    assert unit["remote"]["phase"] == "submitted"
    assert unit["remote"]["has_status_url"] is True
    assert unit["remote"]["has_cancel_url"] is False
    assert "token=secret" not in json.dumps(body)

    assert client.get("/api/generate/nope/diagnostics").status_code == 404
