"""One JSON-over-HTTP call for the OpenAI-compatible clients.

The prompt optimizer and the AI Assistant speak the same protocol with
different error types, so the request, the peer-address check, and the bounded
read live here and each caller translates the failure into its own error.
"""

import asyncio
import json
import logging
from typing import Any

import aiohttp

from ..core import settings as config
from ..core import validators as ssrf
from .session_pool import TIMEOUT_PROMPT_OPTIMIZER, get_pool
from .upstream.errors import UpstreamApiError
from .upstream.transport import read_limited_text_response

logger = logging.getLogger(__name__)


class UpstreamJsonError(Exception):
    """A JSON POST failed. Callers map this onto their own error type."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.timed_out = timed_out


async def post_json(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    prefix: str,
    timeout_seconds: float,
    max_response_bytes: int,
    timeout_kind: str = TIMEOUT_PROMPT_OPTIMIZER,
    peer_label: str | None = None,
) -> dict[str, Any]:
    """POST a JSON body and return the decoded response.

    ``prefix`` names the peer in both the messages and the peer-address
    validation label, so each caller keeps the wording it had.
    """
    try:
        session = get_pool().get(timeout_kind=timeout_kind)
        async with session.post(
            url,
            json=payload,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(
                total=timeout_seconds,
                connect=min(float(timeout_seconds), 10.0),
                sock_connect=min(float(timeout_seconds), 10.0),
                sock_read=timeout_seconds,
            ),
        ) as resp:
            ssrf.validate_response_peer_ip(resp, peer_label or f"{prefix} endpoint")
            if resp.status != 200:
                logger.warning("%s upstream error: status=%d", prefix, resp.status)
                raise UpstreamJsonError(
                    f"{prefix} upstream returned HTTP {resp.status}",
                    status=resp.status,
                )
            try:
                response_text = await read_limited_text_response(
                    resp,
                    max_response_bytes,
                    label=f"{prefix} response",
                )
                return json.loads(response_text)
            except UpstreamApiError as e:
                raise UpstreamJsonError(str(e)) from e
            except Exception as e:
                raise UpstreamJsonError(f"{prefix} returned non-JSON response") from e
    except (aiohttp.ServerTimeoutError, TimeoutError, asyncio.TimeoutError) as e:
        raise UpstreamJsonError(f"{prefix} request timed out", timed_out=True) from e
    except aiohttp.ClientError as e:
        raise UpstreamJsonError(f"{prefix} connection error: {e}") from e


def json_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers
