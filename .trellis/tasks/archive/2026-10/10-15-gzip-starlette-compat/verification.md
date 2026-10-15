# Verification

- Before fix, CI-equivalent Python 3.12.14 / FastAPI 0.143.0 / Starlette 1.8.0 reproduced test_generate_and_sse_contract failure with body-before-start assertion.
- Full backend suite in sandbox: 967 passed, 22 skipped, 6 failed solely due PermissionError on aiohttp test socket bind. This run collected the initial 28 new middleware cases.
- All six socket tests rerun outside sandbox: 6 passed.
- Final middleware regression suite (31 cases, including three subsequently added empty-body tests): 31 passed on Starlette 1.3.1 and 31 passed on 1.8.0. All 976 non-skipped final cases are therefore covered by passing runs.
- Gallery media plus initial middleware suite: 78 passed on both versions.
- uv pip check: all 44 installed packages compatible.
- Ruff 0.16.10, rules F401,F841 on both changed Python files: passed.
- git diff --check: passed.
- Independent trellis-check agent: no findings.
- Existing FastAPI TestClient httpx deprecation warning remains.

## Commit plan

Single work commit: fix: preserve gzip bypass response ordering across Starlette versions

Files: backend/app/api/middleware.py, backend/tests/test_gzip_middleware.py, .trellis/spec/backend/http-compression.md, .trellis/spec/backend/index.md. Task artifacts will be included by Trellis archive bookkeeping after work commit approval. No push or merge.

Existing untracked bootstrap files are excluded; see preexisting-status.txt.
