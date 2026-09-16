"""Settings derived from other settings, and the one path that changes them."""

import pytest

from backend.app.core import overall_config, settings


@pytest.fixture()
def restore_config():
    """Snapshot every setting so a test can change bases without leaking them."""
    snapshot = {
        name: value for name, value in vars(settings).items() if name.isupper()
    }
    yield
    settings.apply_overrides(snapshot)


def test_runtime_override_moves_derived_settings(restore_config):
    settings.apply_overrides(
        {"MAX_FILE_SIZE_MB": 25},
        explicit=frozenset({"MAX_FILE_SIZE_MB"}),
    )

    assert settings.MAX_FILE_SIZE_MB == 25
    assert settings.MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB == 25
    assert settings.UPSTREAM_MEMORY_BUDGET_MB == 256
    assert settings.MAX_PENDING_EDIT_SOURCE_MB == 100
    assert settings.IMPORT_ARCHIVE_MAX_MB == 500
    assert settings.IMPORT_TEMP_RESERVATION_MAX_MB == 1000

    settings.apply_overrides(
        {"MAX_ACTIVE_GENERATE_JOBS": 8},
        explicit=frozenset({"MAX_FILE_SIZE_MB", "MAX_ACTIVE_GENERATE_JOBS"}),
    )

    assert settings.DB_EXECUTOR_WORKERS == 6
    assert settings.AI_ASSISTANT_MAX_CONCURRENCY == 8

    settings.apply_overrides(
        {"IMAGE_JOB_UNIT_LEASE_SECONDS": 600},
        explicit=frozenset(
            {"MAX_FILE_SIZE_MB", "MAX_ACTIVE_GENERATE_JOBS", "IMAGE_JOB_UNIT_LEASE_SECONDS"}
        ),
    )

    assert settings.IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS == 200.0
    assert (
        settings.IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS
        < settings.IMAGE_JOB_UNIT_LEASE_SECONDS / 2
    )


def test_explicitly_configured_derived_value_is_not_overwritten(restore_config):
    settings.apply_overrides(
        {"IMPORT_ARCHIVE_MAX_MB": 7},
        explicit=frozenset({"IMPORT_ARCHIVE_MAX_MB"}),
    )
    settings.apply_overrides(
        {"MAX_FILE_SIZE_MB": 25},
        explicit=frozenset({"MAX_FILE_SIZE_MB", "IMPORT_ARCHIVE_MAX_MB"}),
    )

    assert settings.IMPORT_ARCHIVE_MAX_MB == 7
    assert settings.IMPORT_TEMP_RESERVATION_MAX_MB == 14
    assert settings.MAX_PENDING_EDIT_SOURCE_MB == 100


def test_explicitly_configured_derived_value_wins_over_an_env_default(restore_config):
    settings.apply_overrides(
        {"UPSTREAM_MEMORY_BUDGET_MB": 4096},
        explicit=frozenset({"UPSTREAM_MEMORY_BUDGET_MB"}),
    )
    settings.apply_overrides(
        {"MAX_FILE_SIZE_MB": 25},
        explicit=frozenset({"MAX_FILE_SIZE_MB", "UPSTREAM_MEMORY_BUDGET_MB"}),
    )

    assert settings.UPSTREAM_MEMORY_BUDGET_MB == 4096
    assert settings.MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB == 25


def test_apply_overrides_rejects_unknown_names():
    with pytest.raises(KeyError, match="Unknown settings"):
        settings.apply_overrides({"MAX_FILE_SIZE_MBB": 1})


def test_empty_update_changes_nothing(restore_config):
    before = {
        name: getattr(settings, name) for name in settings.DERIVED_CONFIG_NAMES
    }

    settings.apply_overrides({})

    assert {name: getattr(settings, name) for name in settings.DERIVED_CONFIG_NAMES} == before


def test_registry_defaults_match_the_runtime_defaults():
    """A registry default that drifts from settings would be shown as a lie."""
    for name in (
        "IMPORT_ARCHIVE_MAX_MB",
        "MAX_PENDING_EDIT_SOURCE_MB",
        "AI_ASSISTANT_MAX_CONCURRENCY",
        "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS",
    ):
        spec = overall_config.OVERALL_CONFIG_BY_NAME[name]
        assert spec.default == str(getattr(settings, name)), name


def test_lease_renew_cadence_is_clamped_below_half_the_lease(restore_config):
    settings.apply_overrides(
        {"IMAGE_JOB_UNIT_LEASE_SECONDS": 120, "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS": 90},
        explicit=frozenset(
            {"IMAGE_JOB_UNIT_LEASE_SECONDS", "IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS"}
        ),
    )

    assert settings.IMAGE_JOB_UNIT_LEASE_RENEW_SECONDS == 30.0
