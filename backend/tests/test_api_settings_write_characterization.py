import json

import pytest

from backend.app.core import secrets
from backend.app.runtime.state import state
from backend.app.services import presets


@pytest.fixture()
def configured_secret_registry(request, monkeypatch):
    monkeypatch.setenv("TEST_GOOD_UPSTREAM_KEY", "good-upstream-secret-value")
    monkeypatch.setenv("TEST_OTHER_UPSTREAM_KEY", "other-upstream-secret-value")
    monkeypatch.setenv("TEST_R2_ACCESS_KEY", "r2-access-secret-value")
    monkeypatch.setenv("TEST_R2_SECRET_KEY", "r2-secret-secret-value")
    monkeypatch.setenv("TEST_NODEIMAGE_KEY", "nodeimage-secret-value")
    secrets.configure_registry(
        json.dumps(
            {
                "good-upstream-secret": {
                    "purpose": "upstream_api",
                    "origin": "https://api.example.com",
                    "env": "TEST_GOOD_UPSTREAM_KEY",
                },
                "other-origin-upstream-secret": {
                    "purpose": "upstream_api",
                    "origin": "https://other.example.com",
                    "env": "TEST_OTHER_UPSTREAM_KEY",
                },
                "good-r2-access-secret": {
                    "purpose": "r2_access_key_id",
                    "origin": "https://account.r2.cloudflarestorage.com",
                    "env": "TEST_R2_ACCESS_KEY",
                },
                "good-r2-secret-secret": {
                    "purpose": "r2_secret_access_key",
                    "origin": "https://account.r2.cloudflarestorage.com",
                    "env": "TEST_R2_SECRET_KEY",
                },
                "good-nodeimage-secret": {
                    "purpose": "nodeimage_api_key",
                    "origin": "https://api.nodeimage.com",
                    "env": "TEST_NODEIMAGE_KEY",
                },
            }
        )
    )
    request.addfinalizer(lambda: secrets.configure_registry(""))


def _save_baseline_settings(client):
    resp = client.post(
        "/api/settings",
        json={
            "preset_name": "Baseline Preset",
            "api_url": "https://api.example.com",
            "api_key": "good-upstream-secret",
            "api_path": "/v1/images/generations",
            "default_model": "gpt-image-2",
            "default_response_format": "url",
            "supports_mask": True,
            "prompt_guard": False,
            "upstream_socks5_proxy": "",
            "webhook_url": "",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_post_settings_422_secret_binding_mutates_runtime_state_in_place_before_reload(
    client, configured_secret_registry
):
    baseline = _save_baseline_settings(client)
    preset_id = baseline["active_preset_id"]

    failed = client.post(
        "/api/settings",
        json={
            "active_preset_id": preset_id,
            "preset_name": "Mutated Unvalidated Preset",
            "api_url": "https://api.example.com",
            "api_key": "other-origin-upstream-secret",
            "api_path": "/v1/responses",
            "default_model": "gpt-5.4",
            "default_response_format": "b64_json",
            "supports_mask": False,
            "prompt_guard": True,
            "upstream_socks5_proxy": "${TEST_UPSTREAM_PROXY_URL}",
            "webhook_url": "${TEST_WEBHOOK_URL}",
        },
    )
    assert failed.status_code == 422
    assert failed.json()["detail"] == "secret_id is not bound to the target origin"

    # CHARACTERIZATION (defect to be fixed in C8-S3):
    # update_settings mutates the live preset dict in state.api_presets and
    # state.upstream_socks5_proxy / state.webhook_url BEFORE calling
    # validate_preset_secret_binding(preset). On 422, persist_api_settings() is
    # skipped, so SQLite retains the old baseline, while in-memory state stays
    # mutated for any caller that reads state without calling load_api_settings()
    # (such as POST /api/settings/presets/{id}/health).
    in_memory_preset = presets.get_active_preset()
    assert in_memory_preset["name"] == "Mutated Unvalidated Preset"
    assert in_memory_preset["api_key"] == "other-origin-upstream-secret"
    assert in_memory_preset["api_path"] == "/v1/responses"
    assert in_memory_preset["default_model"] == "gpt-5.4"
    assert in_memory_preset["prompt_guard"] is True
    assert state.upstream_socks5_proxy == "${TEST_UPSTREAM_PROXY_URL}"
    assert state.webhook_url == "${TEST_WEBHOOK_URL}"

    # apply_api_preset(preset) sits after validate_preset_secret_binding, so
    # flat state.api_key / state.api_path stayed at the baseline while
    # state.api_presets[0] holds the mutated preset.
    assert state.api_key == "good-upstream-secret"

    # Endpoints that do not call load_api_settings() observe the mutated preset.
    health = client.post(f"/api/settings/presets/{preset_id}/health")
    assert health.status_code == 200
    path_check = next(c for c in health.json()["checks"] if c["name"] == "api_path")
    assert "/v1/responses" in path_check["message"]

    # GET /api/settings calls load_api_settings() first, which reloads from the
    # unmodified SQLite row and heals runtime state back to the old values.
    after_get = client.get("/api/settings")
    assert after_get.status_code == 200
    body = after_get.json()
    assert body["api_key_secret_id"] == "good-upstream-secret"
    assert body["api_path"] == "/v1/images/generations"
    assert body["default_model"] == "gpt-image-2"
    assert body["prompt_guard"] is False
    assert body["has_upstream_socks5_proxy"] is False
    assert body["has_webhook_url"] is False
    assert body["presets"][0]["name"] == "Baseline Preset"

    # A fresh load_api_settings() also restores the persisted baseline.
    presets.load_api_settings()
    reloaded = presets.build_settings_response().model_dump()
    assert reloaded["api_key_secret_id"] == "good-upstream-secret"
    assert reloaded["presets"][0]["name"] == "Baseline Preset"
    assert state.upstream_socks5_proxy == ""
    assert state.webhook_url == ""


def test_post_settings_422_secret_binding_immediate_get_and_fresh_load_return_old_values(
    client, configured_secret_registry
):
    baseline = _save_baseline_settings(client)
    preset_id = baseline["active_preset_id"]

    failed = client.post(
        "/api/settings",
        json={
            "active_preset_id": preset_id,
            "preset_name": "Mutated Preset",
            "api_url": "https://api.example.com",
            "api_key": "other-origin-upstream-secret",
            "api_path": "/v1/images/generations",
        },
    )
    assert failed.status_code == 422
    assert failed.json()["detail"] == "secret_id is not bound to the target origin"

    # Immediate GET /api/settings heals via load_api_settings() and returns the
    # persisted baseline values rather than the mutated in-memory preset.
    immediate_get = client.get("/api/settings")
    assert immediate_get.status_code == 200
    body = immediate_get.json()
    assert body["presets"][0]["name"] == "Baseline Preset"
    assert body["api_key_secret_id"] == "good-upstream-secret"

    presets.load_api_settings()
    assert presets.get_active_preset()["name"] == "Baseline Preset"
    assert presets.get_active_preset()["api_key"] == "good-upstream-secret"


def test_post_settings_late_422_in_r2_leaves_earlier_groups_persisted(
    client, configured_secret_registry
):
    baseline = _save_baseline_settings(client)
    preset_id = baseline["active_preset_id"]

    failed = client.post(
        "/api/settings",
        json={
            "active_preset_id": preset_id,
            "preset_name": "Persisted Before R2 422",
            "api_url": "https://api.example.com",
            "api_key": "good-upstream-secret",
            "api_path": "/v1/images/generations",
            "prompt_optimizer": {
                "enabled": True,
                "api_url": "https://api.example.com",
                "model": "gpt-4o",
            },
            "ai_assistant": {
                "enabled": True,
                "vision_model": "gpt-4o-mini",
            },
            "r2_backup": {
                "enabled": True,
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "bucket_name": "my-bucket",
                # Wrong purpose/origin for r2_access_key_id -> 422 in validate_r2_secret_bindings
                "access_key_id": "good-upstream-secret",
                "secret_access_key": "good-r2-secret-secret",
            },
        },
    )
    assert failed.status_code == 422
    assert "not permitted" in failed.json()["detail"]

    # CHARACTERIZATION (C8-S4):
    # update_settings persists the preset group, prompt_optimizer, and ai_assistant
    # in separate DB operations before validating r2_backup, so the late 422 in
    # R2 leaves those earlier groups persisted while r2_backup stays unchanged.
    after = client.get("/api/settings").json()
    assert after["presets"][0]["name"] == "Persisted Before R2 422"
    assert after["prompt_optimizer"]["enabled"] is True
    assert after["prompt_optimizer"]["model"] == "gpt-4o"
    assert after["ai_assistant"]["enabled"] is True
    assert after["r2_backup"]["enabled"] is False
    assert after["r2_backup"]["bucket_name"] == ""

    presets.load_api_settings()
    reloaded = presets.build_settings_response().model_dump()
    assert reloaded["presets"][0]["name"] == "Persisted Before R2 422"
    assert reloaded["prompt_optimizer"]["enabled"] is True
    assert reloaded["ai_assistant"]["enabled"] is True
    assert reloaded["r2_backup"]["enabled"] is False


def test_post_settings_late_422_in_nodeimage_leaves_earlier_groups_including_r2_persisted(
    client, configured_secret_registry
):
    baseline = _save_baseline_settings(client)
    preset_id = baseline["active_preset_id"]

    failed = client.post(
        "/api/settings",
        json={
            "active_preset_id": preset_id,
            "preset_name": "Persisted Before NodeImage 422",
            "api_url": "https://api.example.com",
            "api_key": "good-upstream-secret",
            "api_path": "/v1/images/generations",
            "prompt_optimizer": {
                "enabled": True,
                "api_url": "https://api.example.com",
                "model": "gpt-4o",
            },
            "ai_assistant": {
                "enabled": True,
                "vision_model": "gpt-4o-mini",
            },
            "r2_backup": {
                "enabled": True,
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "bucket_name": "persisted-bucket",
                "access_key_id": "good-r2-access-secret",
                "secret_access_key": "good-r2-secret-secret",
            },
            "nodeimage": {
                "enabled": True,
                # Wrong purpose/origin for nodeimage_api_key -> 422 in validate_nodeimage_secret_binding
                "api_key": "good-upstream-secret",
            },
        },
    )
    assert failed.status_code == 422
    assert "not permitted" in failed.json()["detail"]

    # CHARACTERIZATION (C8-S4):
    # update_settings persists preset, prompt_optimizer, ai_assistant, and r2_backup
    # before validating nodeimage, so a late 422 in nodeimage leaves all four
    # earlier groups persisted while nodeimage remains disabled/unset.
    after = client.get("/api/settings").json()
    assert after["presets"][0]["name"] == "Persisted Before NodeImage 422"
    assert after["prompt_optimizer"]["enabled"] is True
    assert after["ai_assistant"]["enabled"] is True
    assert after["r2_backup"]["enabled"] is True
    assert after["r2_backup"]["bucket_name"] == "persisted-bucket"
    assert after["nodeimage"]["enabled"] is False
    assert after["nodeimage"]["has_api_key"] is False

    presets.load_api_settings()
    reloaded = presets.build_settings_response().model_dump()
    assert reloaded["presets"][0]["name"] == "Persisted Before NodeImage 422"
    assert reloaded["r2_backup"]["enabled"] is True
    assert reloaded["nodeimage"]["enabled"] is False
