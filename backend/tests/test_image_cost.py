import pytest

from backend.app.services import image_cost
from backend.tests.support.contract import *  # noqa: F403


# ── normalize_usage ──────────────────────────────────────────────


def test_normalize_usage_returns_none_for_missing_or_empty_usage():
    assert image_cost.normalize_usage(None) is None
    assert image_cost.normalize_usage({}) is None
    assert image_cost.normalize_usage("not a dict") is None


def test_normalize_usage_handles_images_api_shape_with_details():
    raw = {
        "input_tokens": 50,
        "output_tokens": 1024,
        "total_tokens": 1074,
        "input_tokens_details": {"text_tokens": 50, "image_tokens": 0},
        "output_tokens_details": {"image_tokens": 1024},
    }
    usage = image_cost.normalize_usage(raw)
    assert usage["available"] is True
    assert usage["input_tokens"] == 50
    assert usage["output_tokens"] == 1024
    assert usage["text_input_tokens"] == 50
    assert usage["image_input_tokens"] == 0
    assert usage["image_output_tokens"] == 1024
    assert usage["total_tokens"] == 1074
    assert usage["raw"] == raw


def test_normalize_usage_infers_image_output_without_breakdown():
    usage = image_cost.normalize_usage({"input_tokens": 10, "output_tokens": 500})
    assert usage["image_output_tokens"] == 500
    assert usage["total_tokens"] == 510


def test_normalize_usage_handles_chat_completions_shape():
    usage = image_cost.normalize_usage(
        {"prompt_tokens": 30, "completion_tokens": 800, "total_tokens": 830}
    )
    assert usage["input_tokens"] == 30
    assert usage["output_tokens"] == 800
    assert usage["total_tokens"] == 830


def test_normalize_usage_tolerates_malformed_fields():
    usage = image_cost.normalize_usage(
        {"input_tokens": "not-a-number", "output_tokens": -5, "weird_field": {"nested": True}}
    )
    assert usage is not None
    assert usage["input_tokens"] is None
    assert usage["output_tokens"] is None
    assert usage["raw"]["weird_field"] == {"nested": True}


# ── estimate_image_cost ──────────────────────────────────────────


def test_estimate_image_cost_reports_missing_usage_without_fabricating_zero():
    cost = image_cost.estimate_image_cost("gpt-image-1", None)
    assert cost["estimated_cost_usd"] is None
    assert cost["complete"] is False
    assert cost["rate_source"] == "unknown"
    assert cost["reason"]


def test_estimate_image_cost_reports_unknown_model():
    usage = image_cost.normalize_usage({"input_tokens": 10, "output_tokens": 100})
    cost = image_cost.estimate_image_cost("some-third-party-model", usage)
    assert cost["estimated_cost_usd"] is None
    assert cost["complete"] is False
    assert cost["rate_source"] == "unknown"
    assert "some-third-party-model" in cost["reason"]


def test_estimate_image_cost_uses_builtin_rate_for_known_model():
    usage = image_cost.normalize_usage(
        {
            "input_tokens_details": {"text_tokens": 1_000_000, "image_tokens": 0},
            "output_tokens_details": {"image_tokens": 1_000_000},
        }
    )
    cost = image_cost.estimate_image_cost("gpt-image-1", usage)
    assert cost["rate_source"] == "builtin"
    assert cost["complete"] is True
    assert cost["estimated_cost_usd"] == pytest.approx(5.0 + 40.0)


def test_estimate_image_cost_env_override_takes_priority_over_builtin(monkeypatch):
    monkeypatch.setattr(
        image_cost.config,
        "IMAGE_COST_RATES_JSON",
        '{"gpt-image-1": {"text_input_per_million": 1.0, "image_output_per_million": 2.0}}',
    )
    usage = image_cost.normalize_usage(
        {
            "input_tokens_details": {"text_tokens": 1_000_000, "image_tokens": 0},
            "output_tokens_details": {"image_tokens": 1_000_000},
        }
    )
    cost = image_cost.estimate_image_cost("gpt-image-1", usage)
    assert cost["rate_source"] == "env"
    assert cost["estimated_cost_usd"] == pytest.approx(1.0 + 2.0)


def test_estimate_image_cost_marks_incomplete_when_a_rate_field_is_missing(monkeypatch):
    monkeypatch.setattr(
        image_cost.config,
        "IMAGE_COST_RATES_JSON",
        '{"custom-model": {"image_output_per_million": 40.0}}',
    )
    usage = image_cost.normalize_usage(
        {
            "input_tokens_details": {"text_tokens": 100, "image_tokens": 0},
            "output_tokens_details": {"image_tokens": 200},
        }
    )
    cost = image_cost.estimate_image_cost("custom-model", usage)
    assert cost["complete"] is False
    assert cost["estimated_cost_usd"] is not None
    assert "text_input_tokens" in cost["reason"]


def test_estimate_image_cost_ignores_malformed_env_rates_json(monkeypatch, caplog):
    monkeypatch.setattr(image_cost.config, "IMAGE_COST_RATES_JSON", "{not valid json")
    usage = image_cost.normalize_usage({"output_tokens": 100})
    cost = image_cost.estimate_image_cost("gpt-image-1", usage)
    # Falls back to the builtin rate instead of raising.
    assert cost["rate_source"] == "builtin"


# ── sum_usage / sum_costs ────────────────────────────────────────


def test_sum_usage_returns_none_when_nothing_available():
    assert image_cost.sum_usage([None, None]) is None


def test_sum_usage_sums_available_entries_and_skips_none():
    a = image_cost.normalize_usage({"input_tokens": 10, "output_tokens": 100})
    b = image_cost.normalize_usage({"input_tokens": 5, "output_tokens": 50})
    total = image_cost.sum_usage([a, None, b])
    assert total["input_tokens"] == 15
    assert total["output_tokens"] == 150
    assert total["available"] is True


def test_sum_costs_returns_none_when_nothing_present():
    assert image_cost.sum_costs([None, None]) is None


def test_sum_costs_sums_known_costs_and_flags_partial_estimate():
    known = image_cost.estimate_image_cost(
        "gpt-image-1",
        image_cost.normalize_usage({"output_tokens": 1_000_000}),
    )
    unknown = image_cost.estimate_image_cost("unknown-model", None)
    total = image_cost.sum_costs([known, unknown])
    assert total["estimated_cost_usd"] == pytest.approx(40.0)
    assert total["complete"] is False
    assert "1 of 2" in total["reason"]


def test_sum_costs_is_complete_when_every_entry_is_complete():
    a = image_cost.estimate_image_cost(
        "gpt-image-1", image_cost.normalize_usage({"output_tokens": 500_000})
    )
    b = image_cost.estimate_image_cost(
        "gpt-image-1", image_cost.normalize_usage({"output_tokens": 500_000})
    )
    total = image_cost.sum_costs([a, b])
    assert total["complete"] is True
    assert total["rate_source"] == "builtin"
    assert total["estimated_cost_usd"] == pytest.approx(40.0)


# ── repository round trip (usage/cost persist through units + aggregate) ──


def test_unit_usage_and_cost_persist_and_aggregate_to_parent(tmp_path):
    _configure_runtime(tmp_path)

    image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": "cost-parent", "status": "queued", "n": 2},
        operation="generation",
        request={"prompt": "two images", "n": 2},
        image_units=2,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=2,
        max_queued_generate_jobs=2,
        max_pending_edit_source_bytes=1024 * 1024,
    )

    unit_a = image_jobs_repo.claim_next_image_job_unit(
        worker_id="w1", lease_expires_at="2026-01-01T00:05:00Z", now="2026-01-01T00:00:00Z",
        running_limit=2,
    )
    unit_b = image_jobs_repo.claim_next_image_job_unit(
        worker_id="w1", lease_expires_at="2026-01-01T00:05:00Z", now="2026-01-01T00:00:00Z",
        running_limit=2,
    )

    usage_a = image_cost.normalize_usage({"output_tokens": 1_000_000})
    cost_a = image_cost.estimate_image_cost("gpt-image-1", usage_a)
    image_jobs_repo.complete_image_job_unit(
        unit_a["unit_id"],
        result={"images": [{"image_id": "img-a"}]},
        stage_timings={},
        duration="1.00s",
        completed_at="2026-01-01T00:01:00Z",
        usage=usage_a,
        cost=cost_a,
    )

    # Second unit fails after upstream already returned usage.
    usage_b = image_cost.normalize_usage({"output_tokens": 500_000})
    cost_b = image_cost.estimate_image_cost("gpt-image-1", usage_b)
    image_jobs_repo.fail_image_job_unit(
        unit_b["unit_id"],
        status="error",
        stage="generation_failed",
        message="download failed",
        error="download failed",
        usage=usage_b,
        cost=cost_b,
    )

    stored_unit_a = image_jobs_repo.get_image_job_unit(unit_a["unit_id"])
    assert stored_unit_a["usage"]["output_tokens"] == 1_000_000
    assert stored_unit_a["cost"]["estimated_cost_usd"] == pytest.approx(40.0)

    aggregate = image_jobs_repo.aggregate_image_job_units("cost-parent")
    assert aggregate["usage"]["output_tokens"] == 1_500_000
    assert aggregate["cost"]["estimated_cost_usd"] == pytest.approx(60.0)

    # A parent job upsert that carries the aggregated usage/cost round-trips
    # through the generate_jobs table exactly like stage_timings/images do.
    # upsert_generate_job returns the write-normalized row (usage_json as a
    # JSON string, mirroring stage_timings_json); get_generate_job is what
    # deserializes it back into a "usage" dict for API consumers.
    stored_parent = image_jobs_repo.upsert_generate_job(
        {
            "job_id": "cost-parent",
            "status": "partial_failure",
            "usage": aggregate["usage"],
            "cost": aggregate["cost"],
        }
    )
    assert "1500000" in stored_parent["usage_json"]

    reloaded_parent = image_jobs_repo.get_generate_job("cost-parent")
    assert reloaded_parent["usage"]["output_tokens"] == 1_500_000
    assert reloaded_parent["cost"]["estimated_cost_usd"] == pytest.approx(60.0)


def test_generate_job_without_usage_round_trips_as_null(tmp_path):
    _configure_runtime(tmp_path)

    image_jobs_repo.upsert_generate_job(
        {"job_id": "no-usage-job", "status": "success"}
    )
    stored = image_jobs_repo.get_generate_job("no-usage-job")
    assert "usage" not in stored
    assert "cost" not in stored
