# HTTP Compression

## Scope and owner
`TextOnlyGZipMiddleware` and `_TextGZipResponder` in `backend/app/api/compression.py` own selective response compression. Preserve the existing download-path bypasses and text content-type allowlist.

## ASGI contract
For each HTTP response, send exactly one `http.response.start` before any `http.response.body` or `http.response.pathsend`. SSE (`text/event-stream`), excluded binary types, existing `Content-Encoding`, and status 206 must pass through unchanged. Eligible text remains delegated to Starlette for compression and header updates. Empty and multi-chunk bodies follow the same ordering contract.

## Dependency compatibility
Starlette 1.3.1 forwards excluded response headers on the first body; Starlette 1.8.0 forwards them during response start. Overriding only the start branch and delegating excluded bodies to the superclass can silently drop headers after an upgrade. Own both the start and subsequent passthrough branches, and delegate only responses selected for compression. Do not patch around this in route handlers.

## Verification
Use focused ASGI tests to assert message order, header count, original bytes/chunk boundaries, and terminal body flags for passthrough responses. For compressed text, decompress emitted bytes and verify encoding, length, and Vary behavior. Exercise both dependency versions when changing the responder and run backend contract tests with the CI dependency versions.
