# Auth and Security

## One gate owns access control

Authentication is enforced once, in `access_control_middleware` inside
`register_middleware` (`backend/app/api/middleware.py`). For every request it runs,
in order:

1. Host allowlist — `request_host_allowed(request)` (`ALLOWED_HOSTS` / `PUBLIC_ORIGIN`).
2. IP allowlist — `auth.is_ip_allowed(client_ip)`, skipped for `/health`.
3. CSRF origin — `csrf_origin_allowed(request)`.
4. Access token — when `config.ACCESS_KEY` is set, the request must carry a valid
   `config.ACCESS_KEY_COOKIE_NAME` cookie verified by
   `auth.verify_access_token(...)`, unless the path is in `AUTH_EXEMPT_PATHS`
   (`/`, `/api/access`, `/api/access/status`, `/favicon.ico`, `/favicon.svg`,
   `/health`) or starts with `/_app/`.

Consequence: when `ACCESS_KEY` is configured, a new `/api/...` route is protected
automatically. Do not add your own auth dependency. Exempt a path only by editing
`AUTH_EXEMPT_PATHS` and justify it.

## Primitives live in `backend/app/core/security.py`

- `create_access_token()` / `verify_access_token(token)` — HMAC-SHA256 tokens signed
  with `ACCESS_KEY`, base64url payload + signature, `scope == "access"`, expiry from
  `ACCESS_KEY_SESSION_MINUTES`.
- `get_client_ip(request)`, `is_trusted_proxy(...)`, `is_ip_allowed(...)` — proxy-aware
  client resolution and IP/CIDR allowlisting.
- `validate_proxy_config()` — called at startup; raises if `TRUST_PROXY_HEADERS=true`
  without `TRUSTED_PROXY_IPS`.

## Unlock flow

`backend/app/api/routers/access.py` is the only place a token is issued. It layers a
per-IP SQLite lockout (`repositories/coordination/access.py`) with a process-wide rate
ceiling (`_global_access_rate_retry_after`), optional Turnstile, then sets an
`HttpOnly`, `SameSite=Lax` cookie.

Compare secrets as bytes with `hmac.compare_digest(...)`. The existing code compares
`req.access_key.encode("utf-8")` against `config.ACCESS_KEY.encode("utf-8")` so
non-ASCII input cannot raise a `TypeError` and bypass lockout accounting.

## Related rules

- Startup refuses to run without `ACCESS_KEY` unless `ALLOW_UNAUTHENTICATED=true`
  (`backend/app/api/app.py` lifespan).
- `backend/app/core/redaction.py` redacts secrets from logs and error details as a
  backstop; it is not permission to log secrets.
- The browser sends same-origin credentials (`apiFetch` in
  `frontend/src/lib/api/client.ts`), which satisfies the CSRF origin check.
