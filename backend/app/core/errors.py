"""Errors a service can raise without knowing about HTTP."""


class DomainError(Exception):
    """A failed request the api layer turns into an HTTP response.

    Services raise these instead of importing FastAPI, so the status code and
    the error envelope stay owned by the api layer. ``status_code`` and
    ``detail`` mirror ``HTTPException`` so callers that only inspect those two
    attributes read the same way.
    """

    status_code = 500

    def __init__(self, detail: object | None = None, *, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code


class InvalidRequestError(DomainError):
    """The request is malformed or references something unusable."""

    status_code = 400


class UnprocessableRequestError(DomainError):
    """The request is well formed but fails a semantic check."""

    status_code = 422


class NotFoundError(DomainError):
    status_code = 404


class RateLimitedError(DomainError):
    status_code = 429


class UpstreamError(DomainError):
    """The upstream service answered with an error or an unusable payload."""

    status_code = 502


class UpstreamTimeoutError(DomainError):
    status_code = 504
