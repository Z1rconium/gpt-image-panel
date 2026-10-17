# G3 repositories/gallery + coordination review — baseline a50da7a

## Modules reviewed
| Module | Lines |
|---|---|
| repositories/gallery/queries.py | 1048 |
| repositories/gallery/mutations.py | 1046 |
| repositories/gallery/collections.py, filters.py, sync_state.py | — |
| repositories/coordination/access.py, gallery_jobs.py(760), leases.py, sse_slots.py, workers.py | — |

## Predecessor-refactor verification (10-13)
The coordination split **held**. Five domain modules with distinct ownership; `.trellis/spec/backend/coordination.md` matches the on-disk layout; layer check reports **0 violations** (top-level and deferred); no cross-domain leakage or facade bypass found. No re-audit performed per scope.

## Findings

### F-G3-1 — P2, improvement: three overlapping gallery pagination strategies in one module
- Evidence: `backend/app/repositories/gallery/queries.py:740 _get_gallery_page_rows_on_conn` (147 lines), `:510 _get_gallery_page_rows_by_anchor_on_conn` (98), `:389 _store_gallery_page_anchor_best_effort` (95), plus offset (`:350`) and cursor (`:610`) variants.
- Impact: offset, anchor, and keyset-cursor page readers coexist; the 147-line dispatcher decides among them. Hard to reason about consistency and eviction of stored anchors.
- Remedy sketch: name one pagination strategy as canonical for the HTTP path, turn the others into thin adapters; extract the anchor store/evict logic.
- Effort/risk: medium / medium (pagination correctness is user-visible; needs cursor/anchor regression tests).

### F-G3-2 — P2, improvement: mutations.py mixes CRUD with backfill, orphan GC, and cache invalidation
- Evidence: `mutations.py` insert/update/delete (`:84-859`) plus byte-size backfill (`:331 _backfill_gallery_bytes_from_known_rows_on_conn`, `:356 _backfill_gallery_bytes_from_filenames`, `:411 backfill_missing_gallery_bytes`), orphan file GC (`:914 cleanup_orphan_gallery_files`, 89 lines), thumbnail-cache invalidation (`:1041`).
- Impact: one module owns row mutation, storage reconciliation, file lifecycle, and cache concerns.
- Remedy sketch: split "row mutations" from "file/byte reconciliation + GC".
- Effort/risk: medium / medium.

### F-G3-3 — P3, defect (convention): Chinese docstring in an English-codebase module
- Evidence: `backend/app/repositories/gallery/mutations.py:1041` `invalidate_thumbnail_cache` docstring is Chinese (`从内存缩略图验证缓存中移除…`) while the rest of the module/file tree is English; `.trellis/spec/backend/index.md:41` mandates English docs.
- Impact: convention drift only; no behavior change.
- Remedy sketch: translate the docstring.
- Effort/risk: trivial / trivial.

## Coverage notes
- Seasonal/collection/filter modules (`collections.py`, `filters.py`, `sync_state.py`) not individually flagged; `filters.py` composition is used by both pagination and selection paths but no duplication proven.
- No direct `sqlite3.connect`; all DB access goes through `run_db_operation`.
