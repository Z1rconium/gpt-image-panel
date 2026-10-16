"""Caller-specific transient-error budgets for shared lease renewal."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.app.services import lease_renewal


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("retry_budget, expected_loss_time", [(None, 10.0), (0.0, 15.0)])
async def test_transient_error_respects_callers_expiry_budget(monkeypatch, retry_budget, expected_loss_time):
    now = [0.0]
    attempts = []
    lost = []

    async def sleep(delay):
        now[0] += delay

    async def renew():
        attempts.append(now[0])
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(lease_renewal, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(lease_renewal, "asyncio", SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError))
    options = {} if retry_budget is None else {"minimum_retry_budget_seconds": retry_budget}
    await lease_renewal.renew_lease_until_lost(
        renew,
        interval_seconds=10.0,
        retry_interval_seconds=10.0,
        deadline=lambda: 15.0,
        on_lost=lambda: lost.append(now[0]),
        **options,
    )
    assert lost == [expected_loss_time]
    assert attempts == ([10.0] if retry_budget is None else [10.0, 15.0])
