import hmac
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request, Response

from ...core import security as auth
from ...core import settings as config
from ...integrations.turnstile import turnstile_active, verify_turnstile_token
from ...repositories.coordination import (
    clear_access_failure,
    get_access_lockout,
    record_access_failure,
)
from ...schemas.access import AccessRequest, AccessStatusResponse
from ...runtime.blocking import run_db_operation
from ...runtime.state import state


router = APIRouter()
_ACCESS_FAILURES_MAX_SIZE = 10_000
_ACCESS_ATTEMPTS_WINDOW_SECONDS = 60.0


def _global_access_rate_retry_after(now: float) -> int:
    """Coarse process-wide backstop for the per-IP access lockout.

    The SQLite per-IP lockout is the primary brute-force control. This adds a
    bounded ceiling on total ``/api/access`` attempts per rolling minute so a
    distributed source using many IPs still hits a limit even without a
    reverse-proxy rate limiter. ``ACCESS_GLOBAL_MAX_ATTEMPTS_PER_MINUTE <= 0``
    disables it. State is per-process (see runtime.state) and holds at most
    ``limit`` timestamps, so it cannot grow without bound.
    """
    limit = int(getattr(config, "ACCESS_GLOBAL_MAX_ATTEMPTS_PER_MINUTE", 0) or 0)
    if limit <= 0:
        return 0

    attempts = getattr(state, "access_attempt_times", None)
    if attempts is None:
        attempts = deque()
        state.access_attempt_times = attempts

    cutoff = now - _ACCESS_ATTEMPTS_WINDOW_SECONDS
    while attempts and attempts[0] <= cutoff:
        attempts.popleft()

    if len(attempts) >= limit:
        return max(1, int(_ACCESS_ATTEMPTS_WINDOW_SECONDS - (now - attempts[0])))
    attempts.append(now)
    return 0


@router.get("/api/access/status", response_model=AccessStatusResponse)
async def get_access_status(request: Request):
    if not config.ACCESS_KEY:
        return AccessStatusResponse(authenticated=True)

    expires_at = auth.verify_access_token(
        request.cookies.get(config.ACCESS_KEY_COOKIE_NAME)
    )
    return AccessStatusResponse(
        authenticated=bool(expires_at),
        expires_at=expires_at.isoformat() if expires_at else None,
        turnstile_enabled=turnstile_active(),
        turnstile_site_key=config.TURNSTILE_SITE_KEY if turnstile_active() else None,
    )


@router.post("/api/access", response_model=AccessStatusResponse)
async def unlock_access(req: AccessRequest, request: Request, response: Response):
    if not config.ACCESS_KEY:
        return AccessStatusResponse(authenticated=True)

    global_retry_after = _global_access_rate_retry_after(time.monotonic())
    if global_retry_after:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many access attempts. Try again in "
                f"{global_retry_after} seconds."
            ),
            headers={"Retry-After": str(global_retry_after)},
        )

    client_ip = auth.get_client_ip(request)
    remaining = await run_db_operation(
        get_access_lockout,
        client_ip,
        max_failures=config.ACCESS_MAX_FAILURES,
        lockout_seconds=config.ACCESS_LOCKOUT_SECONDS,
        metric_name="get_access_lockout",
    )
    if remaining:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {remaining} seconds.",
        )

    if turnstile_active():
        token = req.turnstile_token.strip()
        if not token:
            raise HTTPException(
                status_code=400,
                detail="Human verification required",
            )
        verification = await verify_turnstile_token(token, client_ip)
        if not verification.ok:
            await run_db_operation(
                record_access_failure,
                client_ip,
                lockout_seconds=config.ACCESS_LOCKOUT_SECONDS,
                max_entries=_ACCESS_FAILURES_MAX_SIZE,
                metric_name="record_access_failure",
            )
            raise HTTPException(status_code=401, detail="Human verification failed")

    # Compare as bytes: str.compare_digest raises TypeError on non-ASCII input,
    # which previously surfaced as an unauthenticated 500 and skipped the
    # failure accounting below (letting non-ASCII attempts bypass the lockout).
    if not hmac.compare_digest(
        req.access_key.encode("utf-8"),
        config.ACCESS_KEY.encode("utf-8"),
    ):
        await run_db_operation(
            record_access_failure,
            client_ip,
            lockout_seconds=config.ACCESS_LOCKOUT_SECONDS,
            max_entries=_ACCESS_FAILURES_MAX_SIZE,
            metric_name="record_access_failure",
        )
        raise HTTPException(status_code=401, detail="Invalid access key")

    await run_db_operation(
        clear_access_failure,
        client_ip,
        metric_name="clear_access_failure",
    )

    token, expires_at = auth.create_access_token()
    response.set_cookie(
        key=config.ACCESS_KEY_COOKIE_NAME,
        value=token,
        max_age=config.ACCESS_KEY_SESSION_MINUTES * 60,
        expires=config.ACCESS_KEY_SESSION_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=config.ACCESS_COOKIE_SECURE,
        path="/",
    )
    return AccessStatusResponse(
        authenticated=True,
        expires_at=expires_at.isoformat(),
    )
