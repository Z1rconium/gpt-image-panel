"""Phase 2 preset export, import, and ordering contract tests."""

import json

from backend.tests.support.contract import *  # noqa: F403
from backend.tests.support.contract import _settings_payload


def _active_preset(client):
    settings = client.get("/api/settings").json()
    return settings, settings["presets"][0]


def _export_package(client, preset_id):
    response = client.post("/api/settings/presets/export", json={"preset_id": preset_id})
    assert response.status_code == 200, response.text
    return response.json()


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


def _package(presets):
    return {
        "format": "gpt-image-panel-presets",
        "format_version": 1,
        "presets": presets,
    }


def _preset_payload(name, **overrides):
    payload = {
        "name": name,
        "api_url": "https://import.example.com",
        "api_path": "/v1/images/generations",
        "default_model": "vendor/model",
        "default_response_format": "url",
        "supports_mask": True,
        "prompt_guard": False,
        "provider_kind": "openai",
    }
    payload.update(overrides)
    return payload


# ── export ───────────────────────────────────────────────────────────────────


def test_export_package_contains_no_secrets(client, monkeypatch):
    settings, preset = _active_preset(client)
    monkeypatch.setenv("EXPORT_TEST_KEY", "export-super-secret-key")
    saved = client.post(
        "/api/settings",
        json={
            **_settings_payload(settings),
            "api_key": "${EXPORT_TEST_KEY}",
            "provider_kind": "async_json",
            "provider_config": _v2_mapping(),
        },
    )
    assert saved.status_code == 200, saved.text

    package = _export_package(client, preset["id"])
    assert package["format"] == "gpt-image-panel-presets"
    assert package["format_version"] == 1
    assert len(package["presets"]) == 1
    exported = package["presets"][0]
    assert exported["provider_kind"] == "async_json"
    assert exported["provider_config"]["version"] == 2
    assert "api_key" not in json.dumps(package)
    assert "export-super-secret-key" not in json.dumps(package)


def test_export_unknown_preset_is_404(client):
    response = client.post("/api/settings/presets/export", json={"preset_id": "nope"})
    assert response.status_code == 404


# ── import preview ───────────────────────────────────────────────────────────


def test_import_preview_classifies_duplicates_and_errors(client):
    settings = client.get("/api/settings").json()
    existing_name = settings["presets"][0]["name"]

    preview = client.post(
        "/api/settings/presets/import/preview",
        json={
            "package": _package(
                [
                    _preset_payload(existing_name),
                    _preset_payload("Fresh", api_url="https://fresh.example.com"),
                ]
            )
        },
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["valid"] is True
    assert body["items"][0]["duplicate_preset_id"] == settings["presets"][0]["id"]
    assert body["items"][0]["will_reuse_api_key"] is False
    assert body["items"][1]["duplicate_preset_id"] is None
    assert any("API key" in warning for warning in body["items"][1]["warnings"])


def test_import_preview_reports_field_level_errors(client):
    bad = _package([_preset_payload("Broken", api_path="/v1/nope")])
    body = client.post(
        "/api/settings/presets/import/preview", json={"package": bad}
    ).json()
    assert body["valid"] is False
    assert any(issue["path"].startswith("presets.0.api_path") for issue in body["errors"])

    unknown = _package([_preset_payload("Unknown", extra_field=True)])
    body = client.post(
        "/api/settings/presets/import/preview", json={"package": unknown}
    ).json()
    assert body["valid"] is False
    assert any("extra_field" in issue["message"] or "extra" in issue["message"].lower() for issue in body["errors"])

    old_version = _package([_preset_payload("Old")])
    old_version["format_version"] = 99
    body = client.post(
        "/api/settings/presets/import/preview", json={"package": old_version}
    ).json()
    assert body["valid"] is False
    assert any(issue["path"] == "format_version" for issue in body["errors"])


def test_import_preview_rejects_corrupt_json_and_oversized_packages(client):
    body = client.post(
        "/api/settings/presets/import/preview", json={"package": "{not json"}
    ).json()
    assert body["valid"] is False
    assert "JSON" in body["errors"][0]["message"]

    oversized = _package([_preset_payload("Big")])
    oversized["presets"][0]["default_model"] = "x" * 200
    oversized["junk"] = "y" * (256 * 1024)
    body = client.post(
        "/api/settings/presets/import/preview", json={"package": oversized}
    ).json()
    assert body["valid"] is False
    assert "too large" in body["errors"][0]["message"]


def test_import_preview_requires_mapping_for_async(client):
    body = client.post(
        "/api/settings/presets/import/preview",
        json={"package": _package([_preset_payload("Async", provider_kind="async_json")])},
    ).json()
    assert body["valid"] is False
    assert body["errors"][0]["path"] == "presets.0.provider_config"


# ── import apply ─────────────────────────────────────────────────────────────


def test_import_creates_new_presets_and_persists(client):
    package = _package(
        [
            _preset_payload("Imported A", api_url="https://a.example.com"),
            _preset_payload("Imported B", api_url="https://b.example.com"),
        ]
    )
    response = client.post(
        "/api/settings/presets/import", json={"package": package, "items": []}
    )
    assert response.status_code == 200, response.text
    settings = response.json()
    names = [preset["name"] for preset in settings["presets"]]
    assert "Imported A" in names and "Imported B" in names
    for preset in settings["presets"]:
        if preset["name"].startswith("Imported"):
            assert preset["has_api_key"] is False
            assert preset["api_key_source"] == "empty"

    reloaded = client.get("/api/settings").json()
    assert {preset["name"] for preset in reloaded["presets"]} >= {"Imported A", "Imported B"}


def test_import_update_requires_explicit_target_and_clears_key_on_origin_change(client, monkeypatch):
    settings, active = _active_preset(client)
    monkeypatch.setenv("UPDATE_ORIGIN_TEST_KEY", "update-origin-secret")
    saved = client.post(
        "/api/settings",
        json={**_settings_payload(settings), "api_key": "${UPDATE_ORIGIN_TEST_KEY}"},
    )
    assert saved.status_code == 200, saved.text

    package = _package(
        [
            _preset_payload(
                active["name"],
                api_url="https://elsewhere.example.com",
                default_model="changed/model",
            )
        ]
    )
    response = client.post(
        "/api/settings/presets/import",
        json={
            "package": package,
            "items": [{"index": 0, "action": "update", "target_preset_id": active["id"]}],
        },
    )
    assert response.status_code == 200, response.text
    settings = response.json()
    updated = next(p for p in settings["presets"] if p["id"] == active["id"])
    assert updated["api_url"] == "https://elsewhere.example.com"
    assert updated["default_model"] == "changed/model"
    assert updated["has_api_key"] is False


def test_import_apply_is_atomic_on_invalid_input(client):
    before = client.get("/api/settings").json()
    response = client.post(
        "/api/settings/presets/import",
        json={
            "package": _package([_preset_payload("Never")]),
            "items": [{"index": 0, "action": "update", "target_preset_id": "missing"}],
        },
    )
    assert response.status_code == 422
    after = client.get("/api/settings").json()
    assert [p["id"] for p in after["presets"]] == [p["id"] for p in before["presets"]]
    assert "Never" not in json.dumps(after)

    response = client.post(
        "/api/settings/presets/import",
        json={
            "package": _package([_preset_payload("Broken", provider_kind="async_json")]),
            "items": [],
        },
    )
    assert response.status_code == 422
    after = client.get("/api/settings").json()
    assert "Broken" not in json.dumps(after)


# ── ordering ─────────────────────────────────────────────────────────────────


def test_preset_order_is_persisted_and_validated(client):
    # Add a second preset so ordering is observable.
    created = client.post("/api/settings/presets", json={})
    assert created.status_code == 200
    settings = created.json()
    ids = [preset["id"] for preset in settings["presets"]]
    assert len(ids) == 2
    reversed_ids = list(reversed(ids))

    response = client.put("/api/settings/presets/order", json={"preset_ids": reversed_ids})
    assert response.status_code == 200, response.text
    assert [preset["id"] for preset in response.json()["presets"]] == reversed_ids
    assert [preset["id"] for preset in client.get("/api/settings").json()["presets"]] == reversed_ids

    bad = client.put(
        "/api/settings/presets/order", json={"preset_ids": [reversed_ids[0], reversed_ids[0]]}
    )
    assert bad.status_code == 422
    unknown = client.put(
        "/api/settings/presets/order",
        json={"preset_ids": [reversed_ids[0], "missing"]},
    )
    assert unknown.status_code == 422
