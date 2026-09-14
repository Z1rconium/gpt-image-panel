import multiprocessing
import os
import statistics
import time
from collections import Counter
from pathlib import Path

import pytest

from backend.app.core import settings as config
from backend.app.core.observability import metrics
from backend.app.core.utils import utc_now
from backend.app.repositories import db as db_repo
from backend.app.repositories import image_jobs as image_jobs_repo
from backend.app.repositories.gallery import mutations as gallery_mutations
from backend.app.repositories.gallery import queries as gallery_queries
from backend.app.services import blocking


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PERFORMANCE_TESTS") != "true",
    reason="set RUN_PERFORMANCE_TESTS=true to run performance baselines",
)


def _configure_runtime(tmp_path: Path):
    images_dir = tmp_path / "images"
    data_dir = tmp_path / "data"
    images_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config.IMAGES_DIR = str(images_dir)
    config.THUMBNAILS_DIR = str(images_dir / "thumbs")
    config.DATA_DIR = str(data_dir)
    config.DATABASE_FILE = str(data_dir / "app.sqlite3")
    config.DEFAULT_UPSTREAM_SOCKS5_PROXY = ""

    db_repo.close_database_connections()
    metrics.reset()
    db_repo.verify_storage_writable()


def _seed_gallery_rows(row_count: int):
    now_prefix = "2026-05-18T12:"
    sizes = ("1024x1024", "1536x1024", "1024x1536")
    models = ("gpt-image-2", "gpt-image-1")
    presets = ("Default", "Studio", "Draft")
    with db_repo._connect() as conn:
        for start in range(0, row_count, 5_000):
            rows = [
                {
                    "id": f"img-{index:06d}",
                    "prompt": f"benchmark prompt {index % 100} p{index % 10}",
                    "size": sizes[index % len(sizes)],
                    "filename": f"img-{index:06d}.png",
                    "created_at": f"{now_prefix}{index % 60:02d}:{index % 60:02d}",
                    "model": models[index % len(models)],
                    "quality": "auto",
                    "output_format": "png",
                    "n": 1,
                    "api_path": "/v1/images/generations",
                    "api_preset_name": presets[index % len(presets)],
                    "favorite": index % 7 == 0,
                    "bytes": 128 + (index % 8192),
                }
                for index in range(start, min(start + 5_000, row_count))
            ]
            with db_repo._transaction(conn):
                gallery_mutations._insert_gallery_entries_on_conn(conn, rows)


def _seed_job_rows(row_count: int):
    for index in range(row_count):
        image_jobs_repo.upsert_generate_job(
            {
                "job_id": f"job-{index:04d}",
                "status": "success",
                "stage": "completed",
                "message": "completed",
                "operation": "generation",
                "prompt": f"history prompt {index}",
                "size": "1024x1024",
                "created_at": f"2026-05-18T12:{index % 60:02d}:00",
                "updated_at": f"2026-05-18T12:{index % 60:02d}:01",
                "completed_at": f"2026-05-18T20:{index % 60:02d}:01+08:00",
                "model": "gpt-image-2",
                "duration": "1.00s",
            }
        )


def _measure_ms(callback, iterations: int = 30) -> tuple[float, float]:
    durations = []
    for _ in range(iterations):
        started_at = time.perf_counter()
        callback()
        durations.append((time.perf_counter() - started_at) * 1000)
    p50 = statistics.median(durations)
    p95 = statistics.quantiles(durations, n=100, method="inclusive")[94]
    return p50, p95


@pytest.mark.parametrize("row_count", [1_000, 10_000])
def test_gallery_page_query_baseline(tmp_path, row_count, record_property):
    _configure_runtime(tmp_path)
    _seed_gallery_rows(row_count)

    def query():
        page = gallery_queries.get_gallery_page(
            page=1,
            page_size=9,
            filters={"prompt": "benchmark prompt 4"},
            include_total_bytes=True,
        )
        assert page.total > 0

    p50, p95 = _measure_ms(query)
    record_property(f"gallery_{row_count}_rows_p50_ms", round(p50, 2))
    record_property(f"gallery_{row_count}_rows_p95_ms", round(p95, 2))
    assert p95 < 500


@pytest.mark.parametrize("row_count", [50_000, 100_000])
@pytest.mark.parametrize(
    ("case_name", "page", "filters", "include_total_bytes", "include_counts", "include_filter_options"),
    [
        ("first_page_no_prompt", 1, {}, False, True, True),
        ("deep_page_no_prompt", 5_000, {}, False, True, False),
        (
            "combined_filters",
            1,
            {
                "model": "gpt-image-2",
                "preset": "Default",
                "size": "1024x1024",
                "favorite": True,
            },
            False,
            True,
            False,
        ),
        ("short_prompt_like", 1, {"prompt": "p4"}, False, True, False),
        ("total_bytes", 1, {}, True, True, False),
        ("lightweight_cursor_page", 2, {}, False, False, False),
    ],
)
def test_gallery_large_query_baselines(
    tmp_path,
    row_count,
    case_name,
    page,
    filters,
    include_total_bytes,
    include_counts,
    include_filter_options,
    record_property,
):
    _configure_runtime(tmp_path)
    _seed_gallery_rows(row_count)
    cursor = None
    direction = "next"
    if case_name == "lightweight_cursor_page":
        first_page = gallery_queries.get_gallery_page(
            page=1,
            page_size=9,
            filters=filters,
            include_counts=False,
            include_filter_options=False,
        )
        assert first_page.next_cursor
        cursor = first_page.next_cursor

    def query():
        gallery_page = gallery_queries.get_gallery_page(
            page=page,
            page_size=9,
            filters=filters,
            include_total_bytes=include_total_bytes,
            include_counts=include_counts,
            include_filter_options=include_filter_options,
            cursor=cursor,
            direction=direction,
        )
        assert gallery_page.images or page > gallery_page.total_pages
        if include_counts:
            assert gallery_page.total >= 0
        if include_total_bytes:
            assert gallery_page.total_bytes > 0

    p50, p95 = _measure_ms(query, iterations=12)
    prefix = f"gallery_{row_count}_rows_{case_name}"
    record_property(f"{prefix}_p50_ms", round(p50, 2))
    record_property(f"{prefix}_p95_ms", round(p95, 2))
    assert p95 < 3000


def test_gallery_deep_page_anchor_cache_100k(tmp_path, record_property):
    _configure_runtime(tmp_path)
    _seed_gallery_rows(100_000)

    started_at = time.perf_counter()
    first_page = gallery_queries.get_gallery_page(
        page=5_000,
        page_size=9,
        include_filter_options=False,
    )
    first_ms = (time.perf_counter() - started_at) * 1000
    assert first_page.images
    assert first_page.timings_ms.get("anchor_seeded_by_offset") == 1.0

    def query():
        page = gallery_queries.get_gallery_page(
            page=5_000,
            page_size=9,
            include_filter_options=False,
        )
        assert page.images
        assert page.timings_ms.get("anchor_seeded_by_offset", 0.0) == 0.0

    p50, p95 = _measure_ms(query, iterations=12)
    record_property("gallery_100000_rows_deep_page_first_ms", round(first_ms, 2))
    record_property("gallery_100000_rows_deep_page_cached_p50_ms", round(p50, 2))
    record_property("gallery_100000_rows_deep_page_cached_p95_ms", round(p95, 2))
    assert p95 < 1000


@pytest.mark.parametrize(
    ("case_name", "page", "filters"),
    [
        (
            "favorite_model_size",
            200,
            {
                "model": "gpt-image-2",
                "size": "1024x1024",
                "favorite": True,
            },
        ),
        ("prompt_fts", 50, {"prompt": "benchmark prompt 4"}),
    ],
)
def test_gallery_filtered_deep_page_anchor_baselines(
    tmp_path,
    monkeypatch,
    case_name,
    page,
    filters,
    record_property,
):
    _configure_runtime(tmp_path)
    monkeypatch.setattr(gallery_queries, "GALLERY_PAGE_ANCHOR_SMALL_OFFSET_THRESHOLD", 100)
    monkeypatch.setattr(gallery_queries, "GALLERY_PAGE_ANCHOR_INTERVAL_PAGES", 25)
    _seed_gallery_rows(100_000)

    first_page = gallery_queries.get_gallery_page(
        page=page,
        page_size=9,
        filters=filters,
        include_filter_options=False,
    )
    assert first_page.images
    assert first_page.timings_ms.get("anchor_seeded_by_offset") == 1.0

    def query():
        gallery_page = gallery_queries.get_gallery_page(
            page=page,
            page_size=9,
            filters=filters,
            include_filter_options=False,
        )
        assert gallery_page.images
        assert gallery_page.timings_ms.get("anchor_seeded_by_offset", 0.0) == 0.0

    p50, p95 = _measure_ms(query, iterations=12)
    prefix = f"gallery_100000_rows_{case_name}_deep_anchor"
    record_property(f"{prefix}_p50_ms", round(p50, 2))
    record_property(f"{prefix}_p95_ms", round(p95, 2))
    assert p95 < 1000


def test_gallery_cursor_query_baseline(tmp_path, record_property):
    _configure_runtime(tmp_path)
    _seed_gallery_rows(10_000)
    first_page = gallery_queries.get_gallery_page(
        page=1,
        page_size=9,
        filters={"prompt": "benchmark prompt 4"},
    )
    assert first_page.next_cursor

    def query():
        page = gallery_queries.get_gallery_page(
            page=2,
            page_size=9,
            filters={"prompt": "benchmark prompt 4"},
            cursor=first_page.next_cursor,
            direction="next",
        )
        assert page.total > 0

    p50, p95 = _measure_ms(query)
    record_property("gallery_10000_rows_cursor_p50_ms", round(p50, 2))
    record_property("gallery_10000_rows_cursor_p95_ms", round(p95, 2))
    assert p95 < 250


def test_job_history_query_baseline(tmp_path, record_property):
    _configure_runtime(tmp_path)
    _seed_job_rows(500)

    def query():
        rows = image_jobs_repo.list_generate_jobs(limit=50, offset=0)
        assert len(rows) == 50

    p50, p95 = _measure_ms(query)
    record_property("job_history_500_rows_p50_ms", round(p50, 2))
    record_property("job_history_500_rows_p95_ms", round(p95, 2))
    assert p95 < 200


def _multiprocess_dispatch_worker(
    tmp_path_str: str,
    worker_index: int,
    result_queue,
) -> None:
    """Spawn-safe dispatcher: claim/complete every unit until the queue drains.

    Each claim and its completion use the critical write budget, so the only
    way to observe a lost write is a genuine coordination bug rather than a
    transient busy timeout. Runs in a fresh interpreter, so it reconfigures the
    runtime from `tmp_path_str` instead of inheriting parent mutations.
    """
    import asyncio

    _configure_runtime(Path(tmp_path_str))
    worker_id = f"perf-worker-{worker_index}"

    async def dispatch_loop() -> None:
        while True:
            unit = await blocking.run_db_operation(
                image_jobs_repo.claim_next_image_job_unit,
                worker_id=worker_id,
                claim_token=f"{worker_id}:{utc_now()}",
                lease_expires_at="2099-01-01T00:00:00+00:00",
                now=utc_now(),
                running_limit=10_000,
                max_attempts=2,
                metric_name="perf_claim_image_job_unit",
                critical=True,
            )
            if unit is None:
                break
            unit_id = str(unit["unit_id"])
            result_queue.put(("claim", unit_id))
            await blocking.run_db_operation(
                image_jobs_repo.complete_image_job_unit,
                unit_id,
                claim_token=str(unit["claim_token"]),
                result={"images": []},
                stage_timings={},
                duration="0.00s",
                completed_at=utc_now(),
                metric_name="perf_complete_image_job_unit",
                critical=True,
            )

    asyncio.run(dispatch_loop())
    snapshot = metrics.snapshot()
    result_queue.put(("metrics", snapshot["counters"], snapshot["timings_ms"]))


def test_multiprocess_image_unit_claim_no_duplicates(tmp_path, record_property):
    """Two spawned processes share one SQLite file; no unit may be claimed twice
    and the write-lock wait must stay inside the critical busy budget."""
    _configure_runtime(tmp_path)
    unit_count = 60
    image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": "perf-multiprocess-parent", "status": "queued"},
        operation="generation",
        request={"prompt": "perf multiprocess", "n": unit_count},
        image_units=unit_count,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=10_000,
        max_queued_generate_jobs=10_000,
        max_pending_edit_source_bytes=1024 * 1024,
    )
    db_repo.close_database_connections()

    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_multiprocess_dispatch_worker,
            args=(str(tmp_path), index, result_queue),
            name=f"perf-dispatch-{index}",
        )
        for index in range(2)
    ]
    claims: list[str] = []
    counters: Counter = Counter()
    metrics_payloads: list[dict] = []
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=120)
            assert process.exitcode == 0

        for _ in processes:
            while True:
                item = result_queue.get(timeout=30)
                if item[0] == "claim":
                    claims.append(item[1])
                elif item[0] == "metrics":
                    counters.update(item[1])
                    metrics_payloads.append(item[2])
                    break
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        result_queue.close()

    claim_counts = Counter(claims)
    assert len(claims) == unit_count
    assert [count for count in claim_counts.values() if count != 1] == []

    with db_repo._connect() as conn:
        completed = conn.execute(
            "SELECT COUNT(*) FROM image_job_units WHERE status = 'success'"
        ).fetchone()[0]
        retried = conn.execute(
            "SELECT COUNT(*) FROM image_job_units WHERE attempts > 1"
        ).fetchone()[0]
    assert completed == unit_count
    assert retried == 0

    lock_wait_p95 = max(
        (
            payload.get("sqlite.write_lock_wait_ms", {}).get("p95", 0.0)
            for payload in metrics_payloads
        ),
        default=0.0,
    )
    hold_p95 = max(
        (
            payload.get("sqlite.write_txn_hold_ms", {}).get("p95", 0.0)
            for payload in metrics_payloads
        ),
        default=0.0,
    )
    busy_retries = int(counters.get("sqlite.busy_retries", 0))
    record_property("multiprocess_busy_retries", busy_retries)
    record_property("multiprocess_write_lock_wait_p95_ms", round(lock_wait_p95, 2))
    record_property("multiprocess_write_txn_hold_p95_ms", round(hold_p95, 2))

    assert busy_retries <= unit_count
    assert lock_wait_p95 < config.SQLITE_CRITICAL_BUSY_TIMEOUT_MS
    assert hold_p95 < config.SQLITE_CRITICAL_BUSY_TIMEOUT_MS
