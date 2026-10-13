"""Coordination facade and cross-domain persistence contracts."""

import inspect
from datetime import datetime, timedelta, timezone

from backend.app.repositories import coordination
from backend.app.repositories.coordination import access, gallery_jobs, leases, sse_slots, workers
from backend.app.repositories.db.connection import _connect
from backend.tests.support.contract import _configure_runtime


def test_facade_exports_operations_owned_by_each_domain():
    domains = {access, gallery_jobs, leases, sse_slots, workers}
    operations = [getattr(coordination, name) for name in coordination.__all__]

    assert len(coordination.__all__) == len(set(coordination.__all__))
    assert all(inspect.isfunction(operation) for operation in operations)
    assert {inspect.getmodule(operation) for operation in operations} == domains
    for operation in operations:
        owner = inspect.getmodule(operation)
        assert operation is getattr(owner, operation.__name__)

    # Wildcard consumers receive persistence operations, never incidental imports.
    namespace = {}
    exec("from backend.app.repositories.coordination import *", namespace)
    assert set(namespace) - {"__builtins__"} == set(coordination.__all__)
    assert not {"datetime", "timezone", "json", "time", "Any", "Sequence"} & set(namespace)


def test_worker_metrics_observe_sibling_leases_and_clean_expired_sse_slots(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    now_dt = datetime(2026, 10, 13, tzinfo=timezone.utc)
    now = now_dt.isoformat()
    expired = (now_dt - timedelta(seconds=1)).isoformat()
    later = (now_dt + timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(workers, "utc_now", lambda: now)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_dt.astimezone(tz)

    monkeypatch.setattr(workers, "datetime", FixedDatetime)
    for connection_id, expiry in (("expired", expired), ("live", later)):
        assert coordination.acquire_sse_slot(
            client_ip="127.0.0.1",
            connection_id=connection_id,
            lease_expires_at=expiry,
            max_global=3,
            max_per_ip=3,
            now=(now_dt - timedelta(seconds=2)).isoformat(),
        ) == (True, "acquired")
    assert leases.acquire_background_lease(
        name="maintenance", owner="worker-a", lease_expires_at=later, now=now,
    )
    coordination.mark_worker_heartbeat("worker-a", active_units=2)

    # Observation excludes expired slots but leaves cleanup to publication.
    observed = coordination.get_runtime_coordination_metrics()
    assert observed["gauges"]["sse.active_connections"] == 1
    assert observed["gauges"]["sse.expired_slots_cleaned"] == 0
    with _connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sse_slots").fetchone()[0] == 2

    snapshot = {"gauges": {"executor.running": 2}}
    published = coordination.refresh_runtime_coordination_metrics("worker-a", snapshot)
    assert published["gauges"]["sse.active_connections"] == 1
    assert published["gauges"]["sse.expired_slots_cleaned"] == 1
    assert published["gauges"]["workers.active"] == 1
    assert published["gauges"]["workers.active_units"] == 2
    assert published["gauges"]["background_leases.active"] == 1
    assert published["background_leases"][0]["owner"] == "worker-a"
    assert published["workers"] == [{
        "worker_id": "worker-a", "updated_at": now,
        "age_seconds": 0.0, "snapshot": snapshot,
    }]
    assert not sse_slots.release_sse_slot("expired", now=now)
    assert coordination.release_sse_slot("live", now=now)
    assert coordination.release_background_lease(name="maintenance", owner="worker-a", now=now)
