# G8 api layer review — baseline a50da7a

## Modules reviewed
| Module | Lines | Notable |
|---|---|---|
| api/routers/settings.py | 750 | overall config + settings + presets + health |
| api/middleware.py | 642 | gzip + security + errors + host + csrf + registration |
| api/routers/generate.py | 569 | _poll_generate_sse(101) |
| api/routers/gallery_tasks.py | 566 |  |
| api/app.py, contract_app.py, body_limit.py, csp.py, edit_limits.py, responses.py, uploads.py, routers/{access,agent,assistant,edits,gallery,gallery_batch,gallery_collections,gallery_queries,metrics,prompt,prompt_snippets,static}.py | — | — |

## Findings

### F-G8-1 — P2, improvement: routers/settings.py mixes four API surfaces
- Evidence: `backend/app/api/routers/settings.py` — overall-config env registry `get_overall_config:219`, `update_overall_config:225` (64), `_serialize_overall_config:172` (44); user settings `update_settings:292` (92); presets CRUD/import-export `create_settings_preset:450`, `delete_settings_preset:558`, `import_settings_presets:525`; health checks `check_r2_settings_health:393`, `check_settings_preset_health:697` (53), `validate_health_*` (`:612-690`).
- Impact: "server env config", "user settings", "preset management", and "health probing" are four resources in one router module; the health-probing block (`:592-749`) is a distinct concern with its own validation helpers.
- Remedy sketch: split `settings` (config+user) from `presets` and from `health`.
- Effort/risk: low-medium / low-medium (routes are additive; URL paths must stay stable).

### F-G8-2 — P2, improvement: middleware.py is a grab-bag of five unrelated concerns
- Evidence: `backend/app/api/middleware.py` — gzip responder `_TextGZipResponder:74`, `TextOnlyGZipMiddleware:101`; security headers `apply_security_headers:141`; error envelope `error_response:176`, `_error_code_for_status:215`; host/forwarded-host/origin normalization `normalize_origin:232`, `normalize_host:267`, `_configured_allowed_hosts:337`, `request_host_allowed:386`, `get_request_origin:407`, `csrf_origin_allowed:444`; composition `register_middleware:482` (93), `register_exception_handlers:577` (65).
- Impact: host/CSRF/security logic (~300 lines) sits with compression and error formatting. The gzip contract is documented separately (`.trellis/spec/backend/http-compression.md`); host/origin validation overlaps `core/validators.py`/`core/security.py`.
- Remedy sketch: split modules by concern (compression, security-headers/errors, host+csrf, registration) and reuse the core host/origin helpers rather than re-deriving them.
- Effort/risk: medium / medium (security-relevant code; needs focused tests).

## Coverage notes
- `contract_app.py` composition order is consistent with the compression contract; no ordering defect found.
- Long handlers `update_settings` (92) and `_poll_generate_sse` (101) are single-purpose; not reported.
- Layer matrix: 0 violations (routers import services, not repositories).
