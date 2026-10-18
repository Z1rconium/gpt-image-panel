# Independent acceptance: backend Batch 1

## Decision

**Accepted for the Batch 1 staged commit.** The B1/B2/B3 candidate matches the approved deletion/delegation scope and preserves the inspected live contracts. No production or test correction was necessary. This review does not accept the concurrently edited frontend or the subsequent B4 batch; integrated acceptance remains with the main session.

Reviewed the four implementation diffs against `HEAD` (`12a19db`), the new `backend/tests/test_db_row_helpers.py`, `validation-backend-1.md`, and the new backend compatibility specification/index entry. Read the role manifest, requirements/design/implementation plan and research evidence. No Git operations were performed by this reviewer.

## Findings (fixed)

None. No introduced mechanical, behavior or compatibility issue was found in the assigned candidate.

## Findings (not fixed)

No new issue requiring repair or a scope decision was found. The separate baseline SSE logger defect and quoted annotation issue remain recorded in `research/summary.md` and are outside this batch.

## Verified contracts and source evidence

- **Favorite conversion and established imports:** `backend/app/repositories/db/rows.py:227–233,761–762` retains the original integer/string policy through delegation, not truthiness or the distinct tri-state filter parser at `:236–250`. Comparing the original AST bodies proves the two former implementations were identical. The unchanged facade still exports both names at `backend/app/repositories/db/__init__.py:89,92`; direct mutation/settings callers retain their original import paths. The new characterization includes all accepted strings, unsupported filter-only `y`, negative/non-1 integers, numeric strings/floats, containers and actual integer result types.
- **LIKE escaping:** `backend/app/repositories/db/rows.py:301–303,776–777` retains backslash-first escaping and the `%...%` wrapper. The original bodies are AST-identical. `backend/app/repositories/settings.py:223–245` still applies the same SQL `ESCAPE` and query normalization; facade export at `db/__init__.py:87` is unchanged. Combined escape cases and the established aliases are covered by the unchanged characterization expectations.
- **Removed private implementations:** inspected original definitions at `HEAD:backend/app/repositories/db/rows.py:786`, `HEAD:backend/app/services/gallery_job_shared.py:95`, and `HEAD:backend/app/services/gallery_archive_export.py:179`. Repository source/documentation reference searches and facade inspection found no caller, export, decorator, registration or reflective consumer. The diff removes definition statements only, with no removed import or initialization effect. Signed media URLs at current `rows.py:780–785`, best-effort worker persistence/expiry stamping at `gallery_job_shared.py:95–121`, and incremental archive preparation at `gallery_archive_export.py:179` onward remain unchanged.
- **R2 data-carrier boundary:** `backend/app/integrations/r2/config.py:68–72` retains the live class. An AST comparison after excluding the original dead method confirms unchanged decorators, fields, constructor ordering and defaults, including the `etags` default factory. The retained dynamic module export still includes `RemoteKeyLookup`. Current batch consumers at `backend/app/integrations/r2/sync.py:155–209` use its fields and complete batched HEAD fallback. No Python call to `.contains(...)`, method-name lookup, registration or documented method consumer was found. Removing the method introduces no config-to-sync dependency and removes only its two undefined helper references.
- **Specification accuracy:** `.trellis/spec/backend/refactoring-compatibility.md` and its index entry match current source. In particular, `backend/app/main.py:12` imports `contract_app.proxy` (`api/contract_app.py:6`), and `backend/tests/support/contract.py:25` supplies the assistant service alias used by the `assistant_client` patches at `backend/tests/test_assistant_contract.py:59,134–136`. No documentation correction is needed.

## Verification

- **Tests — pass, reused for the unchanged candidate:** `validation-backend-1.md` records 198 characterization cases passing before production edits, then 429 cases passing after edits across the characterization, gallery media/collection, performance regression, R2, secret-registry, access/settings, storage migration and architecture suites. The inspected implementation/test candidate required no correction; these checks remain applicable. No full backend suite was repeated.
- **Scoped lint regression gate — pass, independently checked:** ran Ruff F401/F841/F811/F821 on each of the four implementation files both from `git show HEAD:<path>` and from the candidate, comparing diagnostic path/rule/message identities. Baseline: 65 F401 plus two F821; candidate: the same 65 F401 and no F821/F841/F811. Zero new diagnostics. The raw implementation Ruff command continues to exit 1 for retained baseline F401 debt; this is not global lint cleanliness.
- **New-test lint — pass:** `.venv/bin/ruff check backend/tests/test_db_row_helpers.py --select F401,F841,F811,F821 --no-cache`.
- **TypeCheck — not configured for this backend:** no standalone Python type-check command/configuration is established in the repository. Independently compiled all four candidate sources with Python's `compile()`; this checks syntax, not static types. Frontend TypeScript/Svelte checking belongs to its owner and integrated acceptance.
- **Diff hygiene — pass:** `git diff --check --` for the four implementation paths.

## Next owner

The main session can stage/commit/push Batch 1 and its directly related evidence/specification, excluding unrelated scaffolding and concurrent frontend edits. Continue B4 under its own ownership and run the planned full backend/integrated checks once the remaining candidate is ready.
