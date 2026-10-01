"""Provider mapping authoring assistance contract tests.

Covers the copyable mapping prompt (including that its embedded example passes
the real schema validator) and the validation/sample-extraction endpoint.
"""

import json
import re

from backend.app.schemas.provider import resolve_provider_config
from backend.tests.support.contract import *  # noqa: F403


def _v2_mapping():
    return {
        "version": 2,
        "submit": {
            "path": "/submit",
            "body": {"prompt": "{{prompt}}"},
            "idempotency_header": "Idempotency-Key",
        },
        "poll": {
            "task_id_path": "$.id",
            "url_template": "/jobs/{{task_id}}",
            "status_path": "$.state",
            "done": ["done"],
            "failed": ["failed"],
        },
        "result": {
            "task_id_path": "$.id",
            "url_template": "/jobs/{{task_id}}/result",
            "images_path": "$.images[*].url",
        },
    }


def test_mapping_prompt_endpoint_describes_the_contract(client):
    response = client.get("/api/settings/provider-mapping/prompt")
    assert response.status_code == 200
    prompt = response.json()["prompt"]
    assert "{{prompt}}" in prompt
    assert "{{task_id}}" in prompt
    assert '"version": 2' in prompt
    assert "API key" in prompt


def test_mapping_prompt_example_validates_against_the_real_schema(client):
    prompt = client.get("/api/settings/provider-mapping/prompt").json()["prompt"]
    match = re.search(r'\{\n  "version": 2,.*?\n\}', prompt, re.DOTALL)
    assert match, "the prompt must embed a v2 example"
    resolved = resolve_provider_config(json.loads(match.group(0)))
    assert resolved.version == 2
    assert resolved.poll.task_id_path == "$.data.task_id"
    assert resolved.submit.idempotency_header == "Idempotency-Key"


def test_mapping_validate_reports_schema_errors_with_paths(client):
    body = client.post(
        "/api/settings/provider-mapping/validate",
        json={"provider_config": {"version": 2, "poll": {"url_path": 1}}},
    ).json()
    assert body["valid"] is False
    assert body["errors"]
    assert any(issue["path"].startswith("poll") for issue in body["errors"])


def test_mapping_validate_extracts_samples(client):
    response = client.post(
        "/api/settings/provider-mapping/validate",
        json={
            "provider_config": _v2_mapping(),
            "sample_submit": {"id": "job-9"},
            "sample_poll": {"state": "done"},
            "sample_result": {"images": [{"url": "https://cdn.example.com/a.png"}]},
        },
    )
    assert response.status_code == 200, response.text
    extraction = response.json()["extraction"]
    assert extraction["task_id"]["found"] is True
    assert extraction["task_id"]["value"] == "job-9"
    assert extraction["status_url"]["found"] is True
    assert extraction["status_value"]["found"] is True
    assert "done" in extraction["status_value"]["detail"]
    assert extraction["image_count"] == 1


def test_mapping_validate_reports_missing_task_id(client):
    body = client.post(
        "/api/settings/provider-mapping/validate",
        json={"provider_config": _v2_mapping(), "sample_submit": {"other": 1}},
    ).json()
    assert body["extraction"]["task_id"]["found"] is False
    assert "no task id" in body["extraction"]["task_id"]["detail"]
