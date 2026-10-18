# Pre-change validation baseline

Date: 2026-10-18. Source: `main` at `12a19db`. No tracked application edits during these checks.

## Backend

- `.venv/bin/python -m pytest backend/tests -q`: **987 passed, 22 skipped, 6 failed**, 100.72 seconds.
- All six failures are in `test_agent_client_transport.py`: aiohttp local test-server `bind(('127.0.0.1', 0))` raises `PermissionError: [Errno 1] Operation not permitted` in the filesystem/network sandbox. See `baseline-backend.log`.
- Reran only that file with approved loopback-capable execution: `.venv/bin/python -m pytest backend/tests/test_agent_client_transport.py -q`: **6 passed**, 0.43 seconds. See `baseline-socket.log`.
- Combined checks account for **993 passing cases and 22 existing opt-in skips**; these are two runs, not a single unrestricted full-suite pass.
- Two upstream TestClient deprecation warnings exist before cleanup. No dependency migration is included.
- `.venv/bin/python -m pip check`: no broken requirements.
- Python 3.11.15 locally; CI/runtime use Python 3.12. Existing CI remains the Python 3.12 check path.

## Frontend

- `npm --prefix frontend run check`: **0 errors, 0 warnings**.
- `npm --prefix frontend run test:unit`: **206 passed across 19 files**.
- `npm --prefix frontend run build`: passed.
- `npm --prefix frontend run check:bundle`: passed; homepage JS **121,166 B gzip / 122,880 B budget**, CSS **14,222 B / 14,336 B**, OGL **15,159 B / 32,276 B**. Preserve budgets and lazy-loading boundaries.
- `npm --prefix frontend run check:worker-build`: passed.
- Logs: `baseline-frontend.log`, `baseline-build.log`.
- Node 24.18.0 and local frontend node_modules present. Playwright's expected Chromium executable exists. Browser acceptance has not been run during planning; it belongs to the candidate after frontend changes.

## Tooling

- `sh -n deploy/buildx-builder.sh`: passed.
- `node --check frontend/scripts/bundle-budget.mjs` and `check-worker-build.mjs`: passed.
- No deployment, dependency installation, server start, production data access, commit or publication occurred.
