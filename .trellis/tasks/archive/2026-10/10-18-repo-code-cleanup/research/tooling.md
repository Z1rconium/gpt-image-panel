# Research: build, test and deployment tooling

- Date: 2026-10-18
- Baseline: main at 12a19db.
- Scope: tracked root build/deployment files, `.github/workflows/`, `deploy/`, frontend scripts and tool configs. Read-only product investigation.

## Coverage and current contracts

Read root package.json, requirements.txt, backend/requirements-dev.txt, Dockerfile, .dockerignore, .gitignore, docker-compose.yml, docker-bake.hcl; all three workflow files and three deploy files; frontend package.json, Vite/Svelte/TypeScript/Vitest/Playwright configs and both frontend scripts. Reviewed archived audit/checklist and Wave E final outcome. Requirements lockfiles are preserved, not manually refactored. Product/readme documents are contract context rather than rewrite targets. Local runtime is Python 3.11.15 / Node 24.18.0; CI/runtime Python is 3.12. `pip check` passes.

The pre-existing worktree contains untracked platform/Trellis scaffolding and an untracked `gpt-with-taste/` project. Do not stage, rewrite or remove these. Runtime data/images and ignored generated/cache files are also outside cleanup scope.

## T1 — P3 improvement: unused bundle predicate

- Anchor: `frontend/scripts/bundle-budget.mjs:73–77` defines `isEntryNamed` with no call sites. Repository search finds only its declaration. It is not exported; script is an ESM CLI.
- Remedy: remove the unused function only. Retain homepage root selection (:81), dependency traversal, manifest interpretation, budget values and output/error behavior unchanged.
- Acceptance: `node --check frontend/scripts/bundle-budget.mjs`; production build then `npm --prefix frontend run check:bundle` and `check:worker-build`; compare report fields and success/failure semantics for the same built inputs.

## Retained intentionally

- Playwright configs share boilerplate but differ in dev/production server commands, test selection, workers, projects, ports and reporter. Factoring them adds indirection without a demonstrated maintenance problem.
- nginx proxy blocks have similar headers but different body limits, timeout/buffering/caching and limit_conn behavior. No deployment rewrite for cosmetic deduplication.
- Buildx cache policies explicitly preserve state and platform split; do not shorten them or invoke builder recreation during cleanup.
- Pinned release inputs, package versions, security floors, environment defaults and bundle budgets remain unchanged. Existing deployment image/version alignment and stale documentation references are separate release/docs concerns, not behavior-preserving code simplification.
- `.github/workflows/lint.yml` intentionally has a scoped unused-code gate, excluding compatibility facades. Do not enable broad lint rule changes without a measured cleanup and preserving those facades.

## Validation during planning

- `sh -n deploy/buildx-builder.sh`: passes.
- `node --check` for both frontend scripts: passes.
- Existing backend suite, frontend check/unit/build/budget/Worker baselines run separately; see baseline.md once completed.

## Relevant guidance

`CLAUDE.md`; `.trellis/spec/backend/testing.md`; `.trellis/spec/guides/code-review-thinking-guide.md`; `.trellis/spec/frontend/jobs-lifecycle.md`.
