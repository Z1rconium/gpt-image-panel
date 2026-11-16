import asyncio

import pytest

from backend.app.core import settings as config
from backend.app.core.errors import UnprocessableRequestError
from backend.app.runtime import state as runtime_state
from backend.app.runtime.state import state
from backend.app.services import preset_package, presets


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    runtime_state.reset()
    yield
    runtime_state.reset()


def _seed_preset_state():
    preset = {
        "id": "preset-1",
        "name": "Original",
        "api_url": "https://api.example.com",
        "api_key": "orig-key",
        "api_path": "/v1/images/generations",
        "default_model": "gpt-image-2",
        "default_response_format": "url",
        "supports_mask": True,
        "prompt_guard": False,
        "provider_kind": "openai",
        "provider_config": {"nested": {"flag": True}},
    }
    state.api_presets = [preset]
    presets.apply_api_preset(preset)
    state.upstream_socks5_proxy = "socks5://127.0.0.1:1080"
    state.webhook_url = "https://hooks.example.com/hook"
    return preset


def test_api_settings_write_holds_reload_guard_and_clears_pending():
    _seed_preset_state()
    gen_before = int(getattr(state, "api_settings_write_generation", 0))

    with presets.api_settings_write():
        assert state.api_settings_write_pending is True
        assert state.api_settings_write_generation == gen_before + 1

    assert state.api_settings_write_pending is False
    assert state.api_settings_write_generation == gen_before + 1


def test_api_settings_write_restores_deep_state_on_exception():
    preset = _seed_preset_state()

    with pytest.raises(RuntimeError, match="db write failed"):
        with presets.api_settings_write():
            preset["name"] = "Mutated In Place"
            preset["provider_config"]["nested"]["flag"] = False
            state.api_presets.append({"id": "preset-2", "name": "Extra"})
            presets.apply_api_preset(
                {
                    "id": "preset-2",
                    "api_url": "https://mutated.example.com",
                    "api_key": "mutated-key",
                    "api_path": "/v1/responses",
                    "default_model": "gpt-5.4",
                    "default_response_format": "b64_json",
                }
            )
            state.upstream_socks5_proxy = "socks5://127.0.0.1:9999"
            state.webhook_url = "https://other.example.com/hook"
            raise RuntimeError("db write failed")

    assert state.api_settings_write_pending is False
    assert len(state.api_presets) == 1
    assert state.api_presets[0]["name"] == "Original"
    assert state.api_presets[0]["provider_config"]["nested"]["flag"] is True
    assert state.active_preset_id == "preset-1"
    assert state.api_url == "https://api.example.com"
    assert state.api_key == "orig-key"
    assert state.api_path == "/v1/images/generations"
    assert state.default_model == "gpt-image-2"
    assert state.default_response_format == "url"
    assert state.upstream_socks5_proxy == "socks5://127.0.0.1:1080"
    assert state.webhook_url == "https://hooks.example.com/hook"


def test_api_settings_write_deletes_previously_unset_attributes_on_rollback():
    assert not hasattr(state, "api_presets")
    assert not hasattr(state, "webhook_url")

    with pytest.raises(ValueError, match="boom"):
        with presets.api_settings_write():
            state.api_presets = [{"id": "new", "name": "New"}]
            state.active_preset_id = "new"
            state.webhook_url = "https://hooks.example.com/new"
            raise ValueError("boom")

    assert not hasattr(state, "api_presets")
    assert not hasattr(state, "active_preset_id")
    assert not hasattr(state, "webhook_url")
    assert state.api_settings_write_pending is False


def test_api_settings_write_works_across_await_and_restores_on_async_error():
    _seed_preset_state()

    async def failing_write():
        with presets.api_settings_write():
            assert state.api_settings_write_pending is True
            state.api_presets[0]["name"] = "Async Mutated"
            state.active_preset_id = "preset-async"
            await asyncio.sleep(0)
            raise RuntimeError("async persist error")

    with pytest.raises(RuntimeError, match="async persist error"):
        asyncio.run(failing_write())

    assert state.api_settings_write_pending is False
    assert state.api_presets[0]["name"] == "Original"
    assert state.active_preset_id == "preset-1"


def test_api_settings_write_rollback_on_error_false_preserves_state_until_s3():
    _seed_preset_state()

    with pytest.raises(RuntimeError, match="late error"):
        with presets.api_settings_write(rollback_on_error=False):
            state.api_presets[0]["name"] = "Kept Until S3"
            raise RuntimeError("late error")

    assert state.api_settings_write_pending is False
    assert state.api_presets[0]["name"] == "Kept Until S3"


def test_apply_preset_import_rolls_back_runtime_state_if_persist_fails(monkeypatch):
    preset = _seed_preset_state()

    def failing_persist():
        raise RuntimeError("sqlite busy")

    monkeypatch.setattr(preset_package, "persist_api_settings", failing_persist)

    package = {
        "format": "gpt-image-panel-presets",
        "format_version": 1,
        "presets": [
            {
                "name": "Updated Name",
                "api_url": "https://new.example.com",
                "api_path": "/v1/responses",
                "default_model": "gpt-5.4",
                "default_response_format": "b64_json",
                "supports_mask": False,
                "prompt_guard": True,
                "provider_kind": "openai",
            }
        ],
    }

    with pytest.raises(RuntimeError, match="sqlite busy"):
        preset_package.apply_preset_import(
            package,
            [{"index": 0, "action": "update", "target_preset_id": preset["id"]}],
        )

    assert state.api_settings_write_pending is False
    assert state.api_presets[0]["name"] == "Original"
    assert state.api_url == "https://api.example.com"
    assert state.api_key == "orig-key"
    assert state.api_path == "/v1/images/generations"


def test_prompt_optimizer_and_ai_assistant_key_resolvers_share_logic_and_preserve_errors(
    monkeypatch,
):
    monkeypatch.setattr(config, "ALLOW_LEGACY_ENV_REFS", True)
    monkeypatch.delenv("TEST_MISSING_OPT_KEY", raising=False)

    assert presets.resolve_prompt_optimizer_api_key({"api_key": ""}) == ""
    assert (
        presets.resolve_prompt_optimizer_api_key(
            {"api_key": "plain-opt-key", "api_url": "https://api.example.com/v1/chat/completions"}
        )
        == "plain-opt-key"
    )

    with pytest.raises(UnprocessableRequestError, match="TEST_MISSING_OPT_KEY is not set or empty"):
        presets.resolve_prompt_optimizer_api_key(
            {"api_key": "${TEST_MISSING_OPT_KEY}", "api_url": "https://api.example.com"}
        )

    monkeypatch.setattr(
        presets,
        "get_prompt_optimizer_settings",
        lambda: {
            "enabled": True,
            "api_url": "https://api.example.com/v1/chat/completions",
            "api_key": "${TEST_MISSING_OPT_KEY}",
            "model": "gpt-4o-mini",
            "timeout_seconds": 30,
        },
    )

    monkeypatch.setenv("TEST_MISSING_OPT_KEY", "resolved-secret-val")
    assert (
        presets.resolve_prompt_optimizer_api_key(
            {"api_key": "${TEST_MISSING_OPT_KEY}", "api_url": "https://api.example.com"}
        )
        == "resolved-secret-val"
    )
    assert presets.resolve_ai_assistant_api_key({}) == "resolved-secret-val"

    # When resolve_api_key returns empty for an env-var reference, each wrapper
    # keeps its exact historical error message.
    monkeypatch.setattr(presets, "resolve_api_key", lambda *args, **kwargs: "")
    with pytest.raises(UnprocessableRequestError) as opt_exc:
        presets.resolve_prompt_optimizer_api_key(
            {"api_key": "${TEST_MISSING_OPT_KEY}", "api_url": "https://api.example.com"}
        )
    assert str(opt_exc.value) == (
        "Prompt optimizer API Key environment variable TEST_MISSING_OPT_KEY is not set or empty. "
        "Set it in the server environment."
    )

    with pytest.raises(UnprocessableRequestError) as assistant_exc:
        presets.resolve_ai_assistant_api_key({})
    assert str(assistant_exc.value) == (
        "Prompt Optimizer API Key environment variable TEST_MISSING_OPT_KEY is not set or empty "
        "for AI Assistant. Set it in the server environment."
    )
