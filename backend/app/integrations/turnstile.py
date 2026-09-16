"""Cloudflare Turnstile server-side verification client."""

import asyncio
from dataclasses import dataclass

import aiohttp

from ..core import settings as config
from .session_pool import TIMEOUT_PROBE, get_pool


@dataclass(frozen=True)
class TurnstileVerification:
    ok: bool
    error_codes: tuple[str, ...] = ()


def turnstile_active() -> bool:
    return bool(
        config.TURNSTILE_ENABLED and config.TURNSTILE_SITE_KEY and config.TURNSTILE_SECRET_KEY
    )


async def verify_turnstile_token(token: str, client_ip: str | None = None) -> TurnstileVerification:
    """Validate a Turnstile token against the siteverify endpoint.

    Network failures are reported as failed verification so that an outage of
    the verification endpoint never unlocks the panel.
    """
    payload = {"secret": config.TURNSTILE_SECRET_KEY, "response": token}
    if client_ip:
        payload["remoteip"] = client_ip

    try:
        session = get_pool().get(timeout_kind=TIMEOUT_PROBE)
        async with session.post(
            config.TURNSTILE_VERIFY_URL,
            data=payload,
            timeout=aiohttp.ClientTimeout(total=config.TURNSTILE_TIMEOUT_SECONDS),
        ) as resp:
            if resp.status >= 400:
                return TurnstileVerification(
                    ok=False,
                    error_codes=(f"verification_request_failed:HTTP {resp.status}",),
                )
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError) as exc:
        return TurnstileVerification(ok=False, error_codes=(f"verification_request_failed:{exc}",))

    errors = tuple(str(code) for code in data.get("error-codes", []) if isinstance(code, str))
    return TurnstileVerification(ok=bool(data.get("success")), error_codes=errors)
