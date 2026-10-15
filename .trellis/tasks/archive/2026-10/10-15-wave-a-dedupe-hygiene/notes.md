# Wave A — execution notes

Branch: `refactor/wave-a-dedupe-hygiene` · baseline `main` @ `0c45ce0`

## Result per item

| ID | Finding | Status | Notes |
|---|---|---|---|
| R1 | F-G4-2 | done | Six `_normalize_stored_*` collapsed into `_normalize_stored_secret(value, field_name)`; field labels preserved verbatim, so error messages are unchanged. |
| R2 | F-G4-3 | done | `_resolve_busy_policy(critical, retry_busy)` shared by `run_db_operation` and `run_db_operation_in_current_thread`. |
| R3 | F-G9-2 | done (corrected) | The two redaction paths were **not** byte-identical: the log scrub keeps the path and uses `[REDACTED]`, the display mask hides the path and uses `***`. Instead of forcing one output, a single parameterized primitive `redaction.mask_url(url, *, token, invalid, require_scheme, hide_path)` now backs both; output is unchanged. |
| R4 | F-G9-3 | done (corrected) | The "three copies" had different separator/normalization semantics. `validators.split_allowlist(value, *, separators)` + `validators.normalize_host_entry()` are now the single primitives; each site keeps its original separators (comma-only for the upstream host allowlist and `ALLOWED_HOSTS`) so accept/reject behavior is identical. Reused by `overall_config`, `middleware`, `secrets`, and `validators`. |
| R5 | F-G3-3 | done | `repositories/gallery/mutations.py` docstring translated to English. |
| R6 | F-G11-1 | done | `apiFetchRaw` added to `lib/api/client.ts` (returns the raw `Response`; same `credentials: 'same-origin'` default; optional `timeoutMs`). The 3 raw `fetch` sites route through it; caller-owned error/401 handling kept because the body must stay streamable. `lib/api/client.ts` is again the only `fetch` call site. |
| R7 | F-G10-1 | done | `schemas/settings.py` split into `presets.py` / `provider_mapping.py` / `overall_config.py`; `settings.py` keeps the settings-domain DTOs and imports `ApiPresetResponse`/`PresetHealthResponse` from `presets.py`. Import sites updated (no facade re-exports, so F401 lint stays clean). |

## Verification

- Backend: `990 passed, 22 skipped` (976 baseline + 14 new regression tests).
- Frontend: `svelte-check` 0 errors / 0 warnings; `vitest` 206 passed; `vite build` OK.
- Ruff `--select F401,F841` on all changed/linted files: clean.
- R7 equivalence: JSON Schema of all 37 DTOs byte-identical before vs after the split.
- R3/R4 equivalence: explicit output checks for `redact_url`, the log scrub, and the splitters — all unchanged.
- Layer matrix (`test_architecture_boundaries.py`) still green.

## Spec updates

- `.trellis/spec/frontend/forms-and-api.md` gains a short `apiFetchRaw` subsection
  (raw/streaming responses; when to use it; why error/401 handling stays with the caller).
- File is currently untracked in git (like most of the `.trellis/spec/` tree), so the
  edit is not part of the Wave A commits; commit it deliberately if the spec tree is
  being tracked.

## New tests

`backend/tests/test_core_allowlist_redaction.py` — locks `redact_url` output, the shared
`mask_url` primitive, `split_allowlist`/`normalize_host_entry`, the `overall_config`
allowlist validators, and `middleware._host_allowed` / `_configured_allowed_hosts`.
