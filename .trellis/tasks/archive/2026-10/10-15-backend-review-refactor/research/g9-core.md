# G9 core review — baseline a50da7a

## Modules reviewed
| Module | Lines | Notable |
|---|---|---|
| core/validators.py | 531 | SSRF/URL validation + secret-env-ref + masking |
| core/secrets.py | 494 | secret registry/resolution |
| core/settings.py | 467 | env settings |
| core/overall_config.py | 413 | env registry + override application |
| core/security.py | 201 | access tokens, client IP |
| core/redaction.py | 98 | sensitive-text redaction |
| core/{api_paths,cdn,constants,diagnostics,errors,image_cost,image_models,logging_config,media,observability,provider_mapping,provider_mapping_prompt,settings_defaults,streaming,utils}.py | — | — |

## Findings

### F-G9-1 — P2, improvement: validators.py mixes secret-env-ref handling with SSRF/URL validation
- Evidence: `backend/app/core/validators.py:14 get_env_var_ref_name`, `:19 is_malformed_env_var_ref`, `:24 resolve_env_var_ref`, `:32 normalize_secret_env_ref_or_plaintext` (35) — credential-reference parsing — sit beside IP/URL validation `_get_private_ip_ranges:69`, `is_private_ip:118`, `resolve_hostname:131`, `validate_upstream_url:231`, `validate_r2_endpoint_url:285` (47), plus masking helpers `mask_webhook_url:449`, `mask_socks5_proxy_url:503` (28).
- Impact: `validators.py` is the SSRF/URL module but also the home for secret-ref normalization and webhook/proxy masking, which belong with `core/secrets.py` and `core/redaction.py`. A security reviewer looking for URL validation must read credential code in the same file.
- Remedy sketch: move secret-env-ref helpers to `core/secrets.py` and masking helpers to `core/redaction.py`; keep URL/SSRF validation here.
- Effort/risk: low-medium / low (pure moves + import updates).
- Related: settings_store's `_normalize_stored_*` (F-G4-2) call `normalize_secret_env_ref_or_plaintext`, so moving it is a small cross-cutting change.

### F-G9-2 — P3, improvement: duplicated URL redaction / masking
- Evidence: `backend/app/core/redaction.py:40 _redact_url` and `backend/app/core/validators.py:369 redact_url` are two URL-redaction implementations; masking also spread across `validators.mask_webhook_url:449`, `validators.mask_socks5_proxy_url:503`, `services/presets.py:71 mask_key`.
- Impact: redaction rules can diverge between the logging path (`redaction`) and the API-response path (`validators`/`presets`).
- Remedy sketch: one URL-redaction primitive in `core/redaction.py`; masking helpers compose it.
- Effort/risk: low / low.

### F-G9-3 — P3, improvement: host/origin/IP allowlist validation duplicated between core modules
- Evidence: `backend/app/core/overall_config.py:274 _validate_ip_list`, `:287 _validate_host_list`, `:293 _validate_host_or_origin_list`, used by `validate_named_value:250`; the same families are validated in `core/validators.py` (`_parse_host_allowlist:103`, IP literal/private-range logic) and consumed by `api/middleware.py:_configured_allowed_hosts`.
- Impact: host/origin parsing rules live in three places (overall_config, validators, middleware).
- Remedy sketch: route all allowlist parsing through one core helper.
- Effort/risk: low / low.

## Coverage notes
- `core` imports only `core`/`schemas` (layer check 0 violations) — the bottom layer is clean.
- `overall_config.py` (env registry + coercion + apply) is cohesive; not flagged apart from F-G9-3.
- `security.py`, `secrets.py`, `streaming.py` are cohesive; no findings.
