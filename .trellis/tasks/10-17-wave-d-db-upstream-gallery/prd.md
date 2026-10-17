# Wave D: DB / upstream / gallery hotspot extraction

## Goal

Extract the remaining complexity hotspots in the DB, upstream, and gallery
areas so each module owns a single concern, with no external behavior change:

| ID | Finding | Change |
|---|---|---|
| D1 | F-G4-1 | Extract `_ensure_database` DDL into per-domain statements; separate init policy from schema. |
| D2 | F-G2-1, F-G2-2 | Move gallery-entry construction out of `generation.py`; extract a streaming decoder + shared call/normalize helper. |
| D3 | F-G3-1 | Consolidate gallery pagination to one canonical strategy + thin adapters. |
| D4 | F-G3-2 | Split gallery row mutations from byte-backfill + orphan GC + cache invalidation. |
| D5 | F-G7-2 | Shared ZIP streaming/validation helpers for export/import. |

## Background

Source: `.trellis/tasks/10-15-backend-review-refactor/refactor-checklist.md` (Wave D),
`research/report.md`, `research/g2-integrations-upstream.md`, `research/g3-gallery-coordination.md`,
`research/g4-repositories-db.md`, `research/g7-services-gallery.md`. Parent:
`10-15-backend-review-refactor`.

Baseline anchors are from `a50da7a` (10-15 review); the tree has since moved with
Waves A–C, so re-locate each anchor before editing:

- **D1**: `repositories/db/schema.py:73` `_ensure_database` spans `:73-437` —
  idempotency guard + `PRAGMA` + inline `executescript` DDL (`:90`) + FTS + backfills.
- **D2**: `integrations/upstream/generation.py` — `:155 save_gallery_entries_from_upstream_data`
  (147 lines, builds gallery metadata/ids/budgets), `:684 call_image_generation_api` (170),
  `:915 call_image_edit_api` (161), `:304 consume_streaming_image_response` (153, nesting 8).
- **D3**: `repositories/gallery/queries.py` — `:740 _get_gallery_page_rows_on_conn` (147),
  `:510 _get_gallery_page_rows_by_anchor_on_conn` (98), `:389 _store_gallery_page_anchor_best_effort` (95),
  plus offset (`:350`) and cursor (`:610`) readers.
- **D4**: `repositories/gallery/mutations.py` — CRUD (`:84-859`) + byte backfill
  (`:331`, `:356`, `:411`) + `cleanup_orphan_gallery_files` (`:914`) + `invalidate_thumbnail_cache` (`:1041`).
- **D5**: `services/gallery_archive_export.py:62` (203) and `:189` (141); `services/gallery_import_jobs.py:79` (121).

## Requirements

- Pure moves/extractions; preserve every public name's behavior, every route's
  response shape, and every persistence/streaming contract.
- D1: fresh init and existing-DB open must produce identical schema; migration
  and DB tests pass. No schema/data migration behavior change.
- D2: keep pure transport/parse in `generation.py`; move entry/asset construction
  to services; flatten the streaming path. Upstream preview/paste-back unchanged.
- D3: pick one canonical pagination strategy for the HTTP path; make the others
  thin adapters. Cursor/anchor/offset page contract unchanged.
- D4: split row mutations from file/byte reconciliation + orphan GC + cache
  invalidation; gallery mutation + maintenance tests pass.
- D5: shared ZIP streaming/validation helpers; archive export/import ZIP shape unchanged.

## Acceptance Criteria

- AC1 (D1): `_ensure_database` is decomposed into per-domain DDL statements; DB/migration tests pass.
- AC2 (D2): gallery-entry construction lives in services; a shared streaming decoder exists; generation/edit/streaming tests pass.
- AC3 (D3): one canonical pagination strategy; cursor/anchor/offset pagination tests pass.
- AC4 (D4): gallery row mutations split from reconciliation/GC/cache; gallery mutation + maintenance tests pass.
- AC5 (D5): shared ZIP helpers; archive export/import tests pass.
- AC6: backend suite ≥ baseline green; layer matrix 0 violations; ruff F401/F841 clean on touched linted files.

## Out of Scope

- Behavioral changes, new features, route/payload/schema changes.
- Wave E job-framework convergence, and any dependency upgrades.
- Wave D items D6–D9 (tracked in `10-17-wave-d-agent-repos-integrations`).
