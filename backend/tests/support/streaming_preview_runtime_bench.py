"""Synthetic preview retention benchmark; no upstream, database, or image decoding.

Run from the repository root:
    .venv/bin/python -m backend.tests.support.streaming_preview_runtime_bench
"""

import asyncio
import gc
import json
import os
import platform
import statistics
import subprocess
import time
import tracemalloc
import types
from pathlib import Path

from backend.app.services import job_events


def measure(module):
    results = []
    for _ in range(5):
        queue = asyncio.Queue(maxsize=20)
        module.state.generate_job_subscribers = {"benchmark": {queue}}
        module.state.generate_job_preview_cache = {}
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        for sequence in range(400):
            module.publish_generate_job_preview("benchmark", {
                "unit_index": sequence % 10,
                "call_index": 0,
                "sequence": sequence,
                "data_url": "data:image/png;base64," + str(sequence).zfill(6) + "A" * (256 * 1024),
            })
        elapsed = (time.perf_counter() - started) * 1000
        retained, peak = tracemalloc.get_traced_memory()
        slots = len(module.get_generate_job_preview_cache())
        pending = queue.qsize()
        module.clear_generate_job_preview_cache("benchmark")
        while not queue.empty():
            queue.get_nowait()
        gc.collect()
        cleared, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results.append((elapsed, retained, peak, cleared))
    return {
        "median_publish_ms": round(statistics.median(row[0] for row in results), 2),
        "median_retained_mib": round(statistics.median(row[1] for row in results) / 1024 ** 2, 3),
        "median_peak_mib": round(statistics.median(row[2] for row in results) / 1024 ** 2, 3),
        "median_cleared_kib": round(statistics.median(row[3] for row in results) / 1024, 2),
        "cache_slots": slots,
        "queued_events": pending,
    }


if __name__ == "__main__":
    baseline = types.ModuleType("backend.app.services._preview_benchmark_baseline")
    baseline.__package__ = "backend.app.services"
    source = subprocess.check_output([
        "git", "show", "v1.7.3:backend/app/services/job_events.py"
    ], text=True)
    exec(compile(source, "v1.7.3/job_events.py", "exec"), baseline.__dict__)
    cpu = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name")), platform.machine()) if Path("/proc/cpuinfo").exists() else platform.machine()
    print(json.dumps({
        "environment": {"os": platform.platform(), "cpu": cpu, "logical_cpus": os.cpu_count(), "python": platform.python_version()},
        "fixture": {"units": 10, "frames": 400, "frame_kib": 256, "slow_subscribers": 1, "queue_limit": 20, "repetitions": 5},
        "v1.7.3": measure(baseline),
        "working_tree": measure(job_events),
    }, indent=2))
