"""Selective response compression (gzip).

Owns the download-path bypasses and the text content-type allowlist. See
``.trellis/spec/backend/http-compression.md`` for the ASGI ordering contract.
"""

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware, GZipResponder
from starlette.types import Message, Receive, Scope, Send


_GZIP_BYPASS_PATHS = {"/api/download-all"}
_GZIP_BYPASS_PREFIXES = ("/api/image/", "/api/thumb/", "/api/download/")
_GZIP_TEXT_CONTENT_TYPES = {
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "application/problem+json",
    "application/xhtml+xml",
    "application/xml",
    "image/svg+xml",
}
_GZIP_BINARY_CONTENT_TYPES = {
    "application/gzip",
    "application/octet-stream",
    "application/pdf",
    "application/zip",
}


def _is_gzip_text_content_type(value: str) -> bool:
    content_type = value.partition(";")[0].strip().lower()
    if not content_type or content_type == "text/event-stream":
        return False
    if content_type in _GZIP_BINARY_CONTENT_TYPES:
        return False
    if content_type == "image/svg+xml":
        return True
    if content_type.startswith(("image/", "audio/", "video/", "font/")):
        return False
    return (
        content_type.startswith("text/")
        or content_type in _GZIP_TEXT_CONTENT_TYPES
        or content_type.endswith("+json")
        or content_type.endswith("+xml")
    )


class _TextGZipResponder(GZipResponder):
    _passthrough = False

    async def send_with_compression(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.initial_message = message
            headers = Headers(raw=message["headers"])
            self.content_encoding_set = "content-encoding" in headers
            self.content_type_is_excluded = not _is_gzip_text_content_type(
                headers.get("content-type", "")
            )
            self._passthrough = (
                self.content_encoding_set
                or self.content_type_is_excluded
                or message["status"] == 206
            )
            # Own the entire bypass path: Starlette versions differ on whether
            # excluded response headers are sent here or with the first body.
            if self._passthrough:
                await self.send(message)
            return
        if self._passthrough:
            await self.send(message)
            return
        await super().send_with_compression(message)


class TextOnlyGZipMiddleware(GZipMiddleware):
    """Compress text responses while letting image and download streams pass through."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _GZIP_BYPASS_PATHS or path.startswith(_GZIP_BYPASS_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if "gzip" not in headers.get("Accept-Encoding", ""):
            await self.app(scope, receive, send)
            return

        responder = _TextGZipResponder(
            self.app,
            self.minimum_size,
            compresslevel=self.compresslevel,
        )
        await responder(scope, receive, send)
