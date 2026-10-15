# Wave A: low-risk dedupe and hygiene refactors

## Goal

Implement the six Wave A items from the 10-15 backend review's `refactor-checklist.md` — cheap, low-blast-radius duplication and hygiene fixes that make the later (riskier) refactors easier. No behavior change.

## Background

Source of truth: `.trellis/tasks/10-15-backend-review-refactor/refactor-checklist.md` (Wave A table) and `research/report.md`. Baseline: `main` at `0c45ce0`. Parent task: `10-15-backend-review-refactor`.

## Requirements

Preserve all external behavior: HTTP paths/payloads, DB schema/data, SSE names/statuses, security semantics, i18n/visual design. Each item is a pure extraction/dedup unless stated.

| ID | Finding | Change |
|---|---|---|
| R1 | F-G4-2 | Collapse the six byte-identical `_normalize_stored_*` helpers in `repositories/db/settings_store.py` into one `_normalize_stored_secret(value, field_name)`. |
| R2 | F-G4-3 | Factor `_resolve_busy_policy(critical, retry_busy)` in `repositories/db/execution.py`, used by both the async and current-thread variants. |
| R3 | F-G9-2 | Single URL-redaction primitive (one implementation; the other delegates). No change to redacted output. |
| R4 | F-G9-3 | Single host/origin/IP allowlist parser reused by `core/overall_config.py` and `api/middleware.py` instead of three copies. Must keep identical accept/reject behavior. |
| R5 | F-G3-3 | Translate the Chinese docstring at `repositories/gallery/mutations.py:1041` to English. |
| R6 | F-G11-1 | Add `apiFetchBlob` (or `apiFetchRaw`) to `frontend/src/lib/api/client.ts` and route the 3 raw `fetch` call sites (`jobMaskRestore.ts:22`, `galleryDeferredActions.ts:756`, `:810`) through it, preserving binary/header/streaming behavior + credentials/timeout/unauthorized handling. |
| R7 | F-G10-1 | Split `schemas/settings.py` into per-domain DTO modules (settings / presets / provider-mapping / overall-config) with existing import sites updated. Public names preserved via re-exports if that keeps the change smaller. |

## Acceptance Criteria

- AC1 (R1): one normalization helper exists; all call sites updated; settings/secret persistence tests pass.
- AC2 (R2): both execution variants call the shared policy helper; db-execution tests pass.
- AC3 (R3): exactly one URL-redaction implementation remains; redaction/validators tests pass and output is unchanged.
- AC4 (R4): one allowlist parser; overall-config and middleware both use it; security/host/CSRF tests pass with unchanged accept/reject behavior.
- AC5 (R5): docstring is English; no code change.
- AC6 (R6): `grep '[^.a-z]fetch('` under `frontend/src` shows only `api/client.ts`; frontend unit tests + build pass; mask-restore and gallery-download flows behave identically.
- AC7 (R7): `schemas/settings.py` split; all import sites updated; `svelte-check`/pytest green; no API schema/field changes.
- AC8 (all): backend suite green (baseline 976 passed / 22 skipped); frontend checks green; layer matrix still 0 violations.

## Out of Scope

- Any Wave B–E item (lease-renewal unification, module splits, hotspot extraction, job-framework convergence).
- Behavior changes, new features, dependency upgrades, schema/data migrations.
- The spec-guide file (left untracked by user decision).

## Key Decisions

- One task for all six Wave A items (they are all small and independently low-risk); commits split per module group.
- R7 is the largest item; re-export shims from `schemas/settings.py` are acceptable if they keep the change surface small.
