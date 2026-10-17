# Wave C: core and api module splits

## Goal

Split three mixed-responsibility modules so each has a single concern, with no
external behavior change:

| ID | Finding | Change |
|---|---|---|
| C5 | F-G9-1 | Move secret-env-ref parsing/normalization to `core/secrets.py`; move masking helpers (`mask_webhook_url`, `mask_socks5_proxy_url`) to `core/redaction.py`. `core/validators.py` keeps SSRF/URL validation only. |
| C3 | F-G8-1 | Split `api/routers/settings.py` (overall-config + user settings + presets + health) into settings / presets / health routers. |
| C4 | F-G8-2 | Split `api/middleware.py` by concern (compression / security-headers+errors / host+CSRF / registration) and reuse core host/origin helpers where they overlap. |

## Background

Source: `refactor-checklist.md` (Wave C), `research/report.md`, `research/g8-api.md`,
`research/g9-core.md`. Parent: `10-15-backend-review-refactor`. Branch: `refactor/wave-b-c`.

## Requirements

- Pure moves/extractions; preserve every public name's behavior and every route's
  path, methods, request/response models, and status codes.
- C3: OpenAPI paths unchanged; keep router registration order that matters
  (routing precedence) and any module-level dependencies identical.
- C4: preserve the compression contract (`.trellis/spec/backend/http-compression.md`),
  the error envelope shape, security headers, and host/CSRF accept/reject behavior.
- C5: keep `validators` imports working for existing callers (re-export only if it
  does not create a cycle and does not trip F401 in linted files); layer matrix must
  stay at 0 violations.

## Acceptance Criteria

- AC1 (C5): secret-ref helpers live in `core/secrets.py`, masking in
  `core/redaction.py`; `core/validators.py` no longer defines them; import sites
  updated; validators/secrets/redaction tests pass.
- AC2 (C3): `api/routers/settings.py` split into >=3 router modules; OpenAPI paths
  identical; router/API tests pass.
- AC3 (C4): `api/middleware.py` split by concern; host/CSRF/CSP/gzip/error tests
  pass; compression contract preserved.
- AC4: backend suite >= baseline green; layer matrix 0 violations; ruff F401/F841
  clean on touched linted files.

## Out of Scope

- Renaming routes or changing payloads.
- Wave D/E items.
