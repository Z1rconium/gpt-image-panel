# G4 repositories/db review — baseline a50da7a

## Modules reviewed
| Module | Lines |
|---|---|
| repositories/db/migrations.py | 907 |
| repositories/db/rows.py | 894 |
| repositories/db/settings_store.py | 802 |
| repositories/db/connection.py, constants.py, execution.py, schema.py, state.py | — |

## Runtime-cleanliness verification (10-13)
Held. `runtime/*` imports no `repositories` module (layer check 0 violations); SQLite scopes/retry/connection-cleanup live in `repositories/db/execution.py`; `run_db_operation` awaits `asyncio.sleep` and `run_db_operation_in_current_thread` uses `time.sleep` — each correct for its context.

## Findings

### F-G4-1 — P2, improvement: `_ensure_database` is a single 365-line function
- Evidence: `backend/app/repositories/db/schema.py:73 _ensure_database` spans `:73-437`. It runs idempotency guards, `PRAGMA` setup, an inline `executescript` DDL block (`:90` onward), FTS setup, and column/backfill steps in one body.
- Impact: the baseline DDL is embedded mid-function, so schema changes and per-table review are hard; the function mixes "when to init" policy with "what the schema is" DDL.
- Remedy sketch: extract DDL into per-domain statements (constant module strings or a `.sql` resource) and split init-policy from schema application. Note `migrations.py` (907) already owns forward migrations — the boundary between the schema baseline here and migrations there should be stated explicitly.
- Effort/risk: medium / medium (DDL must stay byte-compatible with existing DBs).

### F-G4-2 — P2, improvement: six byte-identical `_normalize_stored_*` helpers + multi-domain module
- Evidence: `backend/app/repositories/db/settings_store.py:55-115` — `_normalize_stored_api_key`, `_normalize_stored_socks5_proxy`, `_normalize_stored_webhook_url`, `_normalize_stored_r2_access_key_id`, `_normalize_stored_r2_secret_access_key`, `_normalize_stored_nodeimage_api_key` are identical except the `field_name` string literal. Module docstring already owns "Settings, secret references, overall config, and API preset persistence".
- Impact: pure copy-paste; a fix to the normalization rule must be applied six times; the module spans four persistence domains.
- Remedy sketch: one `_normalize_stored_secret(value, field_name)`; consider splitting settings vs presets vs overall-config stores.
- Effort/risk: low / low.

### F-G4-3 — P3, improvement: duplicated retry-policy selection in execution.py
- Evidence: `backend/app/repositories/db/execution.py:56-63` and `:97-104` repeat the same `critical → busy_timeout/attempts` selection block in the async and sync variants.
- Impact: the two paths can drift apart silently.
- Remedy sketch: factor a `_resolve_busy_policy(critical, retry_busy)` helper.
- Effort/risk: low / low.

## Coverage notes
- `migrations.py` (907) is a long sequential migration chain with one 133-line step (`_migration_agent_conversations` `:679`) and a deliberate `baseline_legacy_schema` step. Reported as an **open question**, not a finding: whether step-squashing is warranted depends on whether any deployment still runs the legacy baseline (evidence not available from code alone).
- `rows.py` (894) is a cross-table row-assembly helper module; no duplication against `schema.py` proven.
- Module-level `RLock`s in `state.py` (9 locks) are a deliberate process-local cache pattern; not reported.
