"""Provider mapping version 2 schema contract tests.

The v2 mapping adds task-id extraction with URL templates and an optional
idempotency header; version 1 and version-less mappings must keep parsing as
they did before.
"""

import asyncio
import json

import pytest

from backend.app.integrations.upstream import async_provider
from backend.app.core import validators
from backend.app.schemas.provider import resolve_provider_config

V1_CONFIG = {
    "version": 1,
    "auth": {"header": "Authorization", "scheme": "Key"},
    "submit": {"path": "/submit", "body": {"prompt": "{{prompt}}"}},
    "poll": {
        "url_path": "$.status_url",
        "status_path": "$.state",
        "done": ["done"],
        "failed": ["failed"],
    },
    "result": {
        "url_path": "$.response_url",
        "images_path": "$.images[*].url",
        "image_kind": "url",
    },
    "cancel": {"url_path": "$.cancel_url", "method": "PUT"},
}

V2_CONFIG = {
    "version": 2,
    "auth": {"header": "Authorization", "scheme": "Key"},
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
        "interval_seconds": 1,
    },
    "result": {
        "task_id_path": "$.id",
        "url_template": "/jobs/{{task_id}}/result",
        "images_path": "$.images[*].url",
        "image_kind": "url",
    },
    "cancel": {
        "task_id_path": "$.id",
        "url_template": "/jobs/{{task_id}}/cancel",
        "method": "DELETE",
    },
}


def test_v1_mapping_still_resolves_unchanged():
    resolved = resolve_provider_config(V1_CONFIG)
    assert resolved.version == 1
    assert resolved.poll.url_path == "$.status_url"
    assert resolved.poll.task_id_path is None
    assert resolved.submit.idempotency_header == ""


def test_version_less_mapping_defaults_to_v1():
    legacy = {key: value for key, value in V1_CONFIG.items() if key != "version"}
    resolved = resolve_provider_config(legacy)
    assert resolved.version == 1
    assert resolved.poll.url_path == "$.status_url"


def test_v2_mapping_resolves_task_id_templates_and_idempotency_header():
    resolved = resolve_provider_config(V2_CONFIG)
    assert resolved.version == 2
    assert resolved.poll.task_id_path == "$.id"
    assert resolved.poll.url_template == "/jobs/{{task_id}}"
    assert resolved.result.url_template == "/jobs/{{task_id}}/result"
    assert resolved.cancel is not None
    assert resolved.cancel.method == "DELETE"
    assert resolved.submit.idempotency_header == "Idempotency-Key"


def test_v2_requires_explicit_version_and_exactly_one_url_source():
    with pytest.raises(Exception):
        resolve_provider_config({**V2_CONFIG, "version": 1})
    broken = json.loads(json.dumps(V2_CONFIG))
    broken["poll"]["url_path"] = "$.status_url"
    with pytest.raises(Exception):
        resolve_provider_config(broken)
    missing = json.loads(json.dumps(V2_CONFIG))
    del missing["poll"]["url_template"]
    with pytest.raises(Exception):
        resolve_provider_config(missing)


def test_v2_url_template_must_be_a_plain_path_with_task_id():
    broken = json.loads(json.dumps(V2_CONFIG))
    broken["poll"]["url_template"] = "https://evil.example/jobs/{{task_id}}"
    with pytest.raises(Exception):
        resolve_provider_config(broken)
    broken = json.loads(json.dumps(V2_CONFIG))
    broken["poll"]["url_template"] = "/jobs/{{other}}"
    with pytest.raises(Exception):
        resolve_provider_config(broken)


def test_v2_idempotency_header_must_be_a_valid_http_token():
    broken = json.loads(json.dumps(V2_CONFIG))
    broken["submit"]["idempotency_header"] = "bad header"
    with pytest.raises(Exception):
        resolve_provider_config(broken)


def test_task_id_is_encoded_as_a_single_path_segment():
    assert (
        async_provider.render_url_template("/jobs/{{task_id}}/status", "a/b c?d")
        == "/jobs/a%2Fb%20c%3Fd/status"
    )


def test_provider_request_query_keeps_ssrf_checks_and_strict_base_urls(monkeypatch):
    monkeypatch.setattr(validators, "resolve_hostname", lambda host: (host, ["8.8.8.8"]))
    monkeypatch.setattr(async_provider.config, "UPSTREAM_HOST_ALLOWLIST", "queue.example.com")
    url = "https://queue.example.com/submit?prompt=fox&model=image"
    assert asyncio.run(async_provider._validated_upstream_url(url)) == url
    with pytest.raises(ValueError, match="query"):
        validators.validate_upstream_url(url, "queue.example.com")
    with pytest.raises(ValueError, match="query"):
        validators.normalize_upstream_base_url(url)
    for rejected in ("https://queue.example.com/path?q=x#fragment", "https://user:key@queue.example.com/path?q=x",
                     "http://queue.example.com/path?q=x", "https://evil.example.com/path?q=x"):
        with pytest.raises(async_provider.UpstreamApiError):
            asyncio.run(async_provider._validated_upstream_url(rejected))
    monkeypatch.setattr(validators, "resolve_hostname", lambda host: (host, ["127.0.0.1"]))
    with pytest.raises(async_provider.UpstreamApiError, match="private"):
        asyncio.run(async_provider._validated_upstream_url(url))
