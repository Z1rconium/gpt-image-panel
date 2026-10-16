# Implementation plan — Wave A

Ordered by blast radius (smallest first). Run backend tests after the backend items and frontend checks after the frontend item. No behavior change expected anywhere.

## Checklist

1. [ ] R5 — translate `repositories/gallery/mutations.py:1041` docstring to English (trivial, do first to warm up).
2. [ ] R1 — `settings_store.py`: replace the six `_normalize_stored_*` with `_normalize_stored_secret(value, field_name)`; update the 6 call sites (`:242`, `:287-288`, `:371`, `:568/570`, `:573`, `:610`).
3. [ ] R2 — `execution.py`: add `_resolve_busy_policy(critical, retry_busy)`; use in `run_db_operation` and `run_db_operation_in_current_thread`.
4. [ ] R3 — unify URL redaction: pick the canonical implementation (prefer `core/redaction.py`), make the other delegate; verify identical output on sample URLs (userinfo, query secret).
5. [ ] R4 — extract one allowlist parser in core (ip list / host list / host-or-origin list) and reuse from `overall_config.validate_named_value` and `middleware._configured_allowed_hosts`; keep accept/reject identical.
6. [ ] R6 — frontend: add `apiFetchBlob`/`apiFetchRaw` to `lib/api/client.ts` (credentials `same-origin`, default timeout, unauthorized handler, ApiError mapping); route `jobMaskRestore.ts:22`, `galleryDeferredActions.ts:756/810`.
7. [ ] R7 — split `schemas/settings.py` into `settings` / `presets` / `provider_mapping` / `overall_config` modules; update imports; keep names stable (re-export from `schemas/settings.py` if that minimizes churn).
8. [ ] Backend validation: `cd backend && ../.venv/bin/python -m pytest -q` expect ≥976 passed / 22 skipped.
9. [ ] Frontend validation: `npm --prefix frontend run check` (svelte-check) + unit tests + `npm --prefix frontend run build`.
10. [ ] Layer-matrix sanity: `backend/tests/test_architecture_boundaries.py` green (part of the suite).
11. [ ] Spec update: note any convention learned (Phase 3.3).
12. [ ] Commit in module-group batches (backend db dedupe / backend redaction-allowlist / backend schemas split / frontend api client).

## Risky points

- R4 (allowlist) and R3 (redaction) are security-relevant: diff behavior must be provably identical; lean on existing tests, and add a focused test only if coverage is missing.
- R7 touches many import sites: do it with `grep`-driven completeness and rely on the suite + `svelte-check` (frontend) to catch misses.

## Rollback

Each item is an isolated, mechanically-revertable change; commit per group so any item can be reverted independently.
