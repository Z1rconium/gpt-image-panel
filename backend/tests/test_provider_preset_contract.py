import copy

import pytest
from pydantic import ValidationError

from backend.app.core import provider_mapping
from backend.app.schemas.provider import ProviderConfig
from backend.tests.support.contract import *  # noqa: F403
from backend.tests.support.contract import _configure_runtime

QUEUE_CONFIG = {
    "version": 1,
    "auth": {"header": "Authorization", "scheme": "Key"},
    "submit": {
        "path": "/{{model}}",
        "body": {
            "prompt": "{{prompt}}",
            "image_size": {"width": "{{width}}", "height": "{{height}}"},
            "num_images": "{{n}}",
        },
    },
    "poll": {
        "url_path": "$.status_url",
        "status_path": "$.status",
        "done": ["COMPLETED"],
        "failed": ["FAILED"],
        "interval_seconds": 1,
        "timeout_seconds": 60,
    },
    "result": {"url_path": "$.response_url", "images_path": "$.images[*].url"},
    "cancel": {"url_path": "$.cancel_url", "method": "PUT"},
}


def _config(**overrides):
    data = copy.deepcopy(QUEUE_CONFIG)
    data.update(overrides)
    return data


def test_json_path_selects_nested_values_and_wildcards():
    data = {"a": {"items": [{"u": "x"}, {"u": "y"}, {"v": 1}]}, "s": "ok"}
    assert provider_mapping.select_all(data, "$.a.items[*].u") == ["x", "y"]
    assert provider_mapping.select_first(data, "$.a.items[1].u") == "y"
    assert provider_mapping.select_first(data, "$.s") == "ok"
    assert provider_mapping.select_first(data, "$.missing.deeper") is None
    assert provider_mapping.select_all({"a": "not-a-list"}, "$.a[*]") == []


@pytest.mark.parametrize("path", ["a.b", "$..a", "$.a[", "$.a['b']", "$.a b", "$." + "a." * 13 + "a"])
def test_json_path_rejects_unsupported_syntax(path):
    with pytest.raises(provider_mapping.ProviderMappingError):
        provider_mapping.parse_json_path(path)


def test_template_keeps_types_for_whole_placeholders():
    variables = {"prompt": "fox", "n": 2, "width": 1024}
    rendered = provider_mapping.render_template(
        {"p": "{{prompt}}", "n": "{{ n }}", "mixed": "w={{width}}px", "list": ["{{n}}"], "flag": True},
        variables,
    )
    assert rendered == {"p": "fox", "n": 2, "mixed": "w=1024px", "list": [2], "flag": True}


def test_template_rejects_unknown_or_unset_variables():
    with pytest.raises(provider_mapping.ProviderMappingError):
        provider_mapping.render_template("{{secret}}", {"secret": "x"})
    with pytest.raises(provider_mapping.ProviderMappingError):
        provider_mapping.render_template("{{width}}", {"width": None})


def test_provider_config_accepts_queue_style_mapping():
    config = ProviderConfig.model_validate(QUEUE_CONFIG)
    assert config.auth.scheme == "Key"
    assert config.result.image_kind == "url"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c["submit"]["body"].update(prompt="{{api_key}}"),
        lambda c: c["submit"].update(path="/../admin"),
        lambda c: c["submit"].update(path="https://evil.example/x"),
        lambda c: c["submit"].update(path="/{{prompt}}"),
        lambda c: c["submit"].update(body={"{{prompt}}": "x"}),
        lambda c: c["auth"].update(header="Cookie"),
        lambda c: c["auth"].update(scheme="Basic"),
        lambda c: c["poll"].update(url_path="status_url"),
        lambda c: c["poll"].update(done=[]),
        lambda c: c["poll"].update(timeout_seconds=1),
        lambda c: c["result"].update(images_path="$..url"),
        lambda c: c.update(unexpected=True),
        lambda c: c.update(version=2),
    ],
)
def test_provider_config_rejects_unsafe_or_malformed_mappings(mutate):
    data = copy.deepcopy(QUEUE_CONFIG)
    mutate(data)
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate(data)


def test_provider_config_rejects_deep_or_oversized_bodies():
    deep = {"a": 1}
    for _ in range(8):
        deep = {"a": deep}
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate(_config(submit={"path": "", "body": deep}))
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate(
            _config(submit={"path": "", "body": {f"k{i}": "x" for i in range(300)}})
        )


def _settings_body(settings, **extra):
    return {
        "active_preset_id": settings["active_preset_id"],
        "preset_name": "Queue",
        "api_url": "https://queue.example.com",
        "api_path": "/v1/images/generations",
        **extra,
    }


def test_async_preset_round_trip_and_invariants(client):
    settings = client.get("/api/settings").json()
    assert settings["provider_kind"] == "openai"
    assert settings["presets"][0]["provider_kind"] == "openai"
    assert settings["presets"][0]["provider_config"] is None

    missing = client.post("/api/settings", json=_settings_body(settings, provider_kind="async_json"))
    assert missing.status_code == 422
    assert client.get("/api/settings").json()["provider_kind"] == "openai"

    saved = client.post(
        "/api/settings",
        json=_settings_body(
            settings,
            provider_kind="async_json",
            provider_config=QUEUE_CONFIG,
            supports_mask=True,
        ),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["provider_kind"] == "async_json"
    assert body["supports_mask"] is False
    preset = body["presets"][0]
    assert preset["provider_kind"] == "async_json"
    assert preset["supports_mask"] is False
    assert preset["provider_config"]["poll"]["done"] == ["COMPLETED"]
    assert preset["provider_config"]["submit"]["path"] == "/{{model}}"

    # Omitted fields keep the stored mapping; the kind can flip back without losing it.
    kept = client.post("/api/settings", json=_settings_body(settings)).json()
    assert kept["presets"][0]["provider_kind"] == "async_json"
    reverted = client.post("/api/settings", json=_settings_body(settings, provider_kind="openai")).json()
    assert reverted["presets"][0]["provider_kind"] == "openai"
    assert reverted["presets"][0]["provider_config"] is not None
    assert reverted["presets"][0]["supports_mask"] is False


def test_invalid_provider_config_is_rejected_with_no_change(client):
    settings = client.get("/api/settings").json()
    bad = copy.deepcopy(QUEUE_CONFIG)
    bad["submit"]["body"]["prompt"] = "{{not_a_variable}}"
    response = client.post(
        "/api/settings",
        json=_settings_body(settings, provider_kind="async_json", provider_config=bad),
    )
    assert response.status_code == 422
    assert client.get("/api/settings").json()["presets"][0]["provider_kind"] == "openai"


def test_created_preset_copies_provider_from_source(client):
    settings = client.get("/api/settings").json()
    client.post(
        "/api/settings",
        json=_settings_body(settings, provider_kind="async_json", provider_config=QUEUE_CONFIG),
    )
    created = client.post(
        "/api/settings/presets",
        json={"name": "Second", "api_url": "https://queue2.example.com"},
    ).json()
    copy_preset = next(p for p in created["presets"] if p["id"] == created["active_preset_id"])
    assert copy_preset["provider_kind"] == "async_json"
    assert copy_preset["provider_config"]["auth"]["scheme"] == "Key"
    assert copy_preset["supports_mask"] is False

    openai = client.post(
        "/api/settings/presets",
        json={"name": "Plain", "provider_kind": "openai", "source_preset_id": created["presets"][0]["id"]},
    ).json()
    plain = next(p for p in openai["presets"] if p["id"] == openai["active_preset_id"])
    assert plain["provider_kind"] == "openai"


def test_provider_columns_persist_across_restart(tmp_path):
    from backend.app.repositories import db as db_repo
    from backend.app.repositories.settings import load_settings, save_settings

    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    settings = load_settings()
    settings["presets"][0]["provider_kind"] = "async_json"
    settings["presets"][0]["provider_config"] = QUEUE_CONFIG
    save_settings(settings)
    db_repo.close_database_connections()

    reloaded = load_settings()
    preset = reloaded["presets"][0]
    assert preset["provider_kind"] == "async_json"
    assert preset["provider_config"]["cancel"]["method"] == "PUT"
    assert preset["supports_mask"] is False
