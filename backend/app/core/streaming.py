"""A streamed response body, described without an HTTP framework."""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field


@dataclass(frozen=True)
class StreamedBody:
    """Chunks plus the headers to send with them.

    Services build this; the api layer turns it into the framework response, so
    streaming stays testable without a request object.
    """

    chunks: AsyncIterator[bytes] | Iterator[bytes]
    media_type: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EmptyResponse:
    """A response with headers but no body, such as an X-Accel-Redirect."""

    status_code: int
    media_type: str
    headers: dict[str, str] = field(default_factory=dict)
