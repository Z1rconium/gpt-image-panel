# Vision preview resource checks and performance baseline

Run the resource regressions and then the benchmark in a fresh process:

```sh
.venv/bin/python -m pytest backend/tests/test_vision_previews.py backend/tests/test_agent_boundaries.py -q
RUN_PERFORMANCE_TESTS=true .venv/bin/python -m pytest backend/tests/test_performance.py -k vision_preview -q -s
```

The benchmark creates four 2048 × 2048 RGB PNGs from Pillow noise, uses a 1024-pixel preview side and 1 MiB encoded-preview ceiling, and sets the independent decode budget to 256 MiB. It prints a `VISION_PREVIEW_BASELINE` JSON record for each scenario. Compare runs on the same machine with the same Python/Pillow versions and worker configuration; elapsed time has no fixed acceptance threshold.

Recorded on 2026-09-30 in WSL, Python 3.13.5, two image workers:

| Scenario | Requests | Actual decodes | Elapsed ms | Cached string bytes | Peak reserved decode bytes | Peak image workers | Process peak RSS KiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cold | 1 | 1 | 153.60 | 557144 | 70254592 | 1 | 144212 |
| Hot | 1 | 0 | 2.47 | 557144 | 0 | 0 | 144212 |
| Concurrent, same file | 12 | 1 | 120.00 | 557144 | 70254592 | 1 | 145120 |
| Concurrent, distinct files | 4 | 4 | 275.01 | 2228576 | 210763776 | 2 | 191344 |

Peak RSS is the process-wide high-water mark from `resource.getrusage`, including imports, fixture construction and all earlier scenarios; it is not incremental decode RSS or a promise that total process RSS stays below the decode budget. Reserved decode memory includes work queued in the image executor. The budget controls conservative estimates, not the entire application's resident memory. Sampling peaks can miss short spikes, so resource regressions also check live admission and worker overlap directly.

Acceptance checks assert exact decode counts (1 / 0 / 1 / 4), image-worker concurrency no greater than `IMAGE_CPU_CONCURRENCY`, and reserved memory no greater than the configured budget. Additional regressions use a 32 MiB budget to require serial admission of 19 MiB estimates, skip oversized PNGs, and admit JPEGs using their draft dimensions. The tests cover cancelled callers, retry after decode failure, ordered loading limits, configuration invalidation, cache clearing during a decode, TTL, LRU and retained-string accounting.

Counters under `vision_preview.*`: `cache_hits`, `cache_misses`, `shared_decodes`, `decodes`, `budget_waits`, `budget_skips`. The `budget_wait` timing records admission latency. Gauges report `cache_bytes`, `cache_entries`, `decode_memory_bytes`, `inflight`, and `memory_capacity_bytes`. Metrics contain counts, bytes and timing only; they do not include image contents or request parameters.

`MAX_JSON_BODY_MB` follows the registered JSON request-body declaration independently of client Content-Type. Agent concurrency remains per worker. `after` is limited to 0–9223372036854775807; Last-Event-ID must be ASCII digits within the same range. These changes keep existing paths, responses and idempotency identifiers intact.
