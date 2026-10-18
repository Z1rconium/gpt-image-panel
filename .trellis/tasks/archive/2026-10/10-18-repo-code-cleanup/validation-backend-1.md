# Batch 1: backend dead implementation and equivalent row helpers

## Change boundary

- Scope: B1/B2/B3 only. There is no requested behavior gap; the maintenance gap is four obsolete definitions and two duplicated row-helper bodies.
- Owners: `repositories/db/rows.py` owns normalization/escaping and media URL mapping; `services/gallery_job_shared.py` owns gallery progress adapters; `services/gallery_archive_export.py` owns streamed archive preparation; `integrations/r2/config.py` owns the live lookup data carrier.
- Changes: remove three private unreferenced helpers and `RemoteKeyLookup.contains`; retain the class constructor/fields/defaults; delegate the snippet helpers to the existing equal gallery/LIKE helpers. Add `backend/tests/test_db_row_helpers.py` to freeze edge values before refactoring.
- Exclusions: B4 import cleanup, HTTP/SQL/schema/transaction changes, signed media URLs, worker progress and incremental archive algorithms, separate SSE/logger defect, frontend, dependencies, runtime data and Git operations. The main session owns task state and phased commits/pushes; another worker owns frontend edits.
- Equivalence evidence: run the same explicit expected-value tests before and after production edits; run planned affected contracts/performance/R2 tests plus architecture checks; compare scoped Ruff findings with the pre-edit baseline.

## Pre-edit inspection

- Working tree had no tracked diff when this worker began. Existing untracked project scaffolding and unrelated files are outside this batch.
- Read complete four implementation targets, DB facade, live R2 batch algorithm, direct favorite/snippet callers and relevant existing tests/specs.
- Repository-wide search (excluding environments, generated files, historical task records and `gpt-with-taste/`) finds only the three private definitions, no Python `.contains(...)` call, and live constructor/field consumers in `r2/sync.py` and `test_r2_sync.py`. No decorator/registry/reflection/export consumer was found.
- The DB facade still exports both normalization names and the snippet LIKE helper. Existing signed URL, best-effort worker progress and streaming archive code remain the active paths.
- Scoped Ruff baseline: 67 findings = 65 pre-existing F401 imports plus two F821 names inside the obsolete R2 method; no F841/F811. No baseline import cleanup is authorized in this batch.

## Validation

- Before any production edits: `.venv/bin/python -m pytest backend/tests/test_db_row_helpers.py -q` — **198 passed**, 0.09 seconds, two existing TestClient deprecation warnings.
- Characterization freezes 39 favorite inputs across both owner names and both facade aliases, including bool/int/negative/non-1, null/empty/whitespace, all six accepted strings, case/trim variations, unsupported filter-only `y`, numeric strings/floats and containers. Results must remain actual integers. Fourteen LIKE strings cover empty/plain/Unicode/whitespace, slash, backslash, percent, underscore and combined/repeated escapes under both owner names and the existing facade name.
- After production edits, the same characterization cases passed unchanged as part of this focused run:

  ```sh
  .venv/bin/python -m pytest backend/tests/test_db_row_helpers.py backend/tests/test_gallery_media_contract.py backend/tests/test_gallery_collections_contract.py backend/tests/test_performance_regressions.py backend/tests/test_r2_sync.py backend/tests/test_secret_registry_review_regressions.py backend/tests/test_access_settings_contract.py backend/tests/test_storage_migrations_contract.py backend/tests/test_architecture_boundaries.py -q
  ```

  **429 passed**, 19.87 seconds, the same two existing TestClient deprecation warnings. This includes all 198 characterization cases and the planned affected suites plus architecture checks. No sandbox/socket denial occurred in this subset.
- Surviving paths exercised by the existing subset include signed CDN image/thumbnail URLs (`test_public_image_and_thumbnail_base_urls_are_returned`), gallery downloads/ZIP, export progress/download and collection export, worker-only progress persistence and best-effort failure, R2 incremental sync/dry-run/upload/error accounting, favorite filtering/mutations and snippet CRUD/search. The live LIST/HEAD/fallback functions and all lookup fields/defaults are unchanged in the source diff; there is no new config-to-sync import.
- `.venv/bin/ruff check backend/tests/test_db_row_helpers.py --select F401,F841,F811,F821 --no-cache` — passed.
- `.venv/bin/ruff check backend/app/integrations/r2/config.py --select F821 --no-cache` — passed; both obsolete-method undefined references are gone.
- Scoped `ruff check` on the four edited implementation files with `--select F401,F841,F811,F821 --no-cache --output-format json` leaves **65 pre-existing F401** findings, zero F821/F841/F811. Compared diagnostic identities (path/rule/message, ignoring shifted locations) against the saved pre-edit result: no new diagnostics; only the two obsolete-method F821 findings removed. Existing import/facade names remain intact. The scoped command still exits 1 because baseline F401 debt remains.
- `git diff --check --` for the four edited implementation paths — passed. Final implementation diff: two delegation statements added, 54 obsolete/duplicate lines removed; helper signatures/facade exports and active algorithms are preserved.

## Handoff

Batch 1 is ready for the main session's review and phased commit/push. Changed paths are the four implementation files named above, `backend/tests/test_db_row_helpers.py`, and this validation record. No dependency or runtime-data changes. Full backend/integrated checks and B4 belong to subsequent coordinated stages; no full suite was run by this worker. The separate live SSE logger defect and quoted type annotation remain unchanged.
