# GZip response compatibility

## Goal
Restore merge validation and correct HTTP streaming behavior without changing application APIs.

## Background
CI job 115394770751 on run 38425807505 installed Starlette 1.8.0 and FastAPI 0.143.0. It reported 33 failed, 912 passed, 22 skipped. All failures report response body without response start. backend/app/api/middleware.py:74 overrides the response-start branch of GZipResponder; Starlette 1.8 now emits excluded response headers in that branch, whereas local 1.3.1 emits them on the first body.

## Requirements
- R1: Send exactly one response start before any body for excluded, pre-encoded, and partial responses.
- R2: Preserve text-only compression, download bypasses, SSE streaming, and binary passthrough behavior.
- R3: Keep compatibility with the existing local Starlette 1.3.1 and CI Starlette 1.8.0. Prefer a small localized implementation over dependency pinning.

## Acceptance criteria
- Regression tests cover response ordering, SSE chunks, binary passthrough, pre-encoded content, partial responses, and ordinary compressed text as applicable to existing contracts.
- Focused tests pass under both relevant Starlette versions; full backend suite passes under CI versions.
- Dependency consistency passes in the CI-equivalent environment.

## Scope
Modify backend/app/api/middleware.py and focused backend tests; document the middleware compatibility contract if useful. No feature changes, dependency lock refresh, workflow redesign, frontend changes, push or merge.

## Authorization
User approved task creation and continuing the concrete repair after the diagnosis and repair/verification summary. No unresolved user decisions.
