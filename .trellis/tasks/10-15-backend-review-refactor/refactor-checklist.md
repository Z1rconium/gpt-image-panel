# Refactor checklist — baseline a50da7a

Actionable subset of `research/report.md`. Each row is independently verifiable and shaped to become one follow-up Trellis task (one task per "Candidate task" group). Priority: P2 = structural debt, P3 = defer-safe. Nothing here is a demonstrated production defect.

**Recommended sequence** is by risk-adjusted value: cheap dedupes first (fast, low blast radius), then module splits, then hotspot extraction, then the high-risk framework convergence last. Items marked ⚠ touch security/DB/load-bearing code and need focused regression coverage.

---

## Wave A — low-risk dedupe & hygiene (bundle as one task, or three micro-tasks)

| # | Findings | Change | Effort | Risk | Verifiable acceptance |
|---|---|---|---|---|---|
| A1 | F-G4-2 | Collapse six identical `_normalize_stored_*` into `_normalize_stored_secret(value, field_name)`. | low | low | `settings_store.py` has one helper; all six call sites updated; settings/secret persistence tests pass. |
| A2 | F-G4-3 | Factor `_resolve_busy_policy(critical, retry_busy)` used by both execution variants. | low | low | Both variants call the shared helper; `db-execution` regression tests pass. |
| A3 | F-G9-2, F-G9-3 | Single URL-redaction primitive in `core/redaction.py`; single host/origin/IP allowlist parser reused by `overall_config` and `middleware`. | low | low | One redaction impl; one allowlist parser; security/validators tests pass. |
| A4 | F-G3-3 | Translate `mutations.py:1041` docstring to English. | trivial | trivial | Rendering/behavior unchanged. |
| A5 | F-G11-1 | Add `apiFetchBlob`/`apiFetchRaw` to `lib/api/client.ts`; route the 3 raw `fetch` sites. | low | low | `grep '[^.a-z]fetch('` shows only the client; mask-restore + gallery downloads still work (`frontend` unit/build). |
| A6 | F-G10-1 | Split `schemas/settings.py` into `settings` / `presets` / `provider_mapping` / `overall_config` DTO modules. | low | low | Import sites updated; `svelte-check`/pytest green. |

## Wave B — lease-renewal unification (enables later work)

| # | Findings | Change | Effort | Risk | Verifiable acceptance |
|---|---|---|---|---|---|
| B1 | F-G5-3 | Shared "renewing lease/slot holder" (create_task + periodic renew + lost→cancel + cleanup) used by all five loops. | medium | medium | All five sites use the holder; each site's renew/lost behavior covered by its existing tests (image units, agent runs, assistant slots, nodeimage, gallery background lease). |

## Wave C — module splits (each independently verifiable)

| # | Findings | Change | Effort | Risk | Verifiable acceptance |
|---|---|---|---|---|---|
| C1 | F-G5-1 | Split `services/presets.py` into presets / credential view / per-feature settings modules. | medium | medium ⚠ | Settings + preset API responses byte-identical (golden/shape tests); no route changes. |
| C2 | F-G5-2 | Split `services/job_events.py` (state+SSE vs persistence vs webhook→`webhook_service.py`). | medium | medium | Jobs SSE/persistence tests pass; webhook dispatch tests pass. |
| C3 | F-G8-1 | Split `api/routers/settings.py` into settings / presets / health routers. | low-med | low-med | OpenAPI paths unchanged; router/API tests pass. |
| C4 | F-G8-2 | Split `api/middleware.py` by concern; reuse core host/origin helpers. | medium | medium ⚠ | Host/CSRF/CSP/gzip tests pass; compression contract (`http-compression.md`) preserved. |
| C5 | F-G9-1 | Move secret-ref helpers to `core/secrets.py`, masking to `core/redaction.py`. | low-med | low | Import sites updated; validators/secrets/redaction tests pass. |
| C6 | F-G6-1 | Split assistant runtime resolution / slot leasing / JSON transport. | medium | medium | Assistant + agent runtime tests pass; slot-lease behavior unchanged. |
| C7 | F-G6-2 | Split assistant_vision prompt-preview generation from gallery analysis. | low-med | low | Assistant vision tests pass. |

## Wave D — hotspot extraction (DB / upstream / gallery)

| # | Findings | Change | Effort | Risk | Verifiable acceptance |
|---|---|---|---|---|---|
| D1 | F-G4-1 | Extract `_ensure_database` DDL into per-domain statements; separate init policy from schema. | medium | medium ⚠ | Fresh init and existing-DB open both produce identical schema; DB/migration tests pass. |
| D2 | F-G2-1, F-G2-2 | Move gallery-entry construction out of `generation.py`; extract streaming decoder + shared call/normalize. | medium | medium ⚠ | Generation/edit/streaming tests pass; upstream preview/paste-back behavior unchanged. |
| D3 | F-G3-1 | Consolidate gallery pagination to one canonical strategy + thin adapters. | medium | medium | Cursor/anchor/offset pagination tests pass; API page contract unchanged. |
| D4 | F-G3-2 | Split gallery row mutations from byte-backfill + orphan GC + cache invalidation. | medium | medium | Gallery mutation + maintenance tests pass. |
| D5 | F-G7-2 | Shared ZIP streaming/validation helpers for export/import. | medium | medium | Archive export/import tests pass; ZIP shape unchanged. |
| D6 | F-G6-3 | Extract image-lifecycle helpers from `AgentToolExecutor`. | low | medium | Agent tool-execution tests pass. |
| D7 | F-G1-1 | Move mask-file helpers out of `image_jobs.py`. | low | low | Mask tests pass. |
| D8 | F-G1-2 | Document/guard the off-loop contract of `_thumbnail_cpu_slot`. | low | low | Docstring/assertion added; thumbnail tests pass. |
| D9 | F-G10-3 | Route R2 credential reads through `core/secrets`. | low | low | R2 config tests pass. |

## Wave E — high-risk framework convergence (do last, own task, requires sign-off)

| # | Findings | Change | Effort | Risk | Verifiable acceptance |
|---|---|---|---|---|---|
| E1 | F-G7-1 | Extract a shared background-job runtime (claim loop + lease holder + progress/SSE publisher) configured by both image jobs and gallery jobs. | high | high ⚠ | Each system migrated separately behind its existing tests; parallel-run equivalence where feasible; no behavior change to job SSE names/statuses or lease semantics. |

## Conditional / do-not-do-yet

| # | Findings | Decision |
|---|---|---|
| X1 | F-G10-2 | Provider v1/v2 validator duplication — **do not** consolidate unless a v1 deprecation plan exists. Likely a deliberate compatibility contract. |
| X2 | F-G4-1 (migrations.py) | Migration-chain squashing — open question; depends on whether any deployment still runs `baseline_legacy_schema`. Needs operator input, not code evidence. |
| X3 | F-G11-2 (F8) | History-loading cleanup appears fixed; add a targeted regression test (cheap) rather than a refactor. |
| X4 | F5/F6 | `galleryDeferredActions.ts` split and `Workspace.svelte` decomposition remain deferred — no counterexample justifies them yet (predecessor guidance). |

## Explicitly not recommended

- Rewriting `image_jobs.py` or `maskDocument.ts`/`MaskEditorDialog.svelte` for size alone — no mixed responsibility or defect demonstrated.
- Dependency upgrades, schema/data migrations, or feature work — outside review scope.
