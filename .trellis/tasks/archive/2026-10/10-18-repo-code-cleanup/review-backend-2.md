# Independent acceptance: backend Batch 2

## Decision

**B4 source and focused behavior accepted for the staged commit.** No correction was needed. The remaining integrated dependency is the final candidate's full backend suite on GitHub/Python 3.12; the main session owns obtaining that result after pushing this batch.

Reviewed the eight B4 implementation diffs against `12a19db`, their callers/composition, import initialization owners, compatibility specification and `validation-backend-2.md`. No source, Git, task-state or unrelated-file changes were made by this reviewer.

## Findings (fixed)

None. The candidate contains the approved import/local cleanup only.

## Findings (not fixed)

No new defect or interface decision was found. The known SSE logger and quoted-annotation issues remain outside this behavior-preserving scope.

## Independent source checks

- **Import-only boundary proved:** compared the five screened modules' ASTs with `12a19db` after excluding top-level import statements. All remaining syntax trees are identical: functions, annotations/defaults, decorators, constants and router/registration code. Removed imported aliases are exactly 80 in `gallery_queries.py`, 75 in `gallery_batch.py`, 59 in `gallery_tasks.py`, 35 in `assistant_batch.py` and 34 in `assistant_text.py` (283 total).
- **Consumers screened:** AST-scanned backend application/tests for direct imports from these five modules, aliased module-attribute accesses and named lookup/patch arguments against the removed-name sets. No removed-name consumer matched. Read the shared contract aliases and inspected the related repository-wide textual/reflection references; `support.contract.gallery_queries` names the persistence module, while `gallery_queries_router` names the cleaned router. These distinct modules were not conflated. The five cleaned modules have no wildcard export/registry exposing their copied import blocks.
- **Patch surfaces retained:** current `backend/app/api/routers/gallery_queries.py:11–16,33–50` retains DB/query, upload/path-resolution and dispatcher globals used by generation, performance, media and NodeImage tests. `gallery_batch.py:60` retains `NODEIMAGE_BATCH_MAX`; `gallery_tasks.py` retains the download cleanup helper. `backend/app/services/assistant_batch.py:16–17` explicitly re-exports the same `assistant_client` module; `assistant_text.py:15` retains its live transport import. Support/test patch targets remain valid.
- **Initialization remains owned:** inspected the live imports through app dispatchers, gallery route composition and assistant runtime/vision services. SSE limiter initialization remains reachable through `gallery_job_sse`; assistant ContextVars remain owned/imported by `assistant_runtime`; archive/job implementations retain their own dependencies. Removed copies do not remove registrations or introduce reverse-layer imports. Reuse the implementer's fresh-interpreter evidence that app loading retains the same 200 local modules and the same complete OpenAPI object (99 paths), together with the inspected import graph; module-count equality alone was not treated as proof.
- **Remaining three edits are local:** `backend/app/api/routers/assistant.py:12–25` still imports both response classes after deleting their earlier duplicate line. `backend/app/api/app.py:25–27` still calls `init_defaults()` before startup; its removed local only read `runtime.state.state.worker_id`, a value already assigned by `runtime/state.py:init_defaults`, with no accessor side effect. `backend/app/api/routers/prompt.py:180–188` retains exception logging and the exact health-error response while dropping an unread exception binding. No route, SQL, transaction, auth, lease, cancellation or error-response policy changed.
- **Specification:** the backend compatibility guide accurately covers this candidate's intentional alias and consumer-screening rule. No extra convention or global lint policy needs to be introduced.

## Verification

- **Lint — pass, independently run:** Ruff F401/F841/F811/F821 on all eight B4 paths reports zero diagnostics. This is scoped acceptance, not a claim of global lint cleanliness.
- **TypeCheck — not configured for backend:** the unchanged Python type-check situation is documented in `review-backend-1.md`. Five import-cleanup sources were independently parsed in the AST comparison; the implementer's record confirms all eight compile. No types/interfaces were altered.
- **Tests — focused pass, valid evidence reused:** `validation-backend-2.md` records 120 architecture/assistant/gallery/NodeImage cases, plus five prompt-optimizer contracts passing (94 deselected). No source correction invalidated these checks. The same app OpenAPI comparison and assistant-client identity check passed in that record.
- **Diff hygiene — pass, independently run:** `git diff --check` over the current tracked candidate.

## Next owner

The main session can commit/push B4 with its record and obtain the final GitHub Python 3.12 full backend result. Earlier Batch 1 full-suite evidence (1191 passed, 22 skipped at `8fa3cbe`) remains evidence for that earlier backend candidate, not a substitute for the post-B4 integrated run. Frontend acceptance is recorded separately in `review-frontend.md` and is unchanged by these backend import edits.
