# Proposed local commits

The user approved the five work-commit groups and Trellis bookkeeping, and explicitly requested a push on 2026-10-13. All five work commits have been created on refactor/architecture-legacy-modules. Push that branch to origin after task/session bookkeeping; preserve the exclusions listed below.

## refactor(db): separate sqlite execution from generic runtime

- backend/app/api/app.py
- backend/app/api/routers/access.py
- backend/app/api/routers/gallery_batch.py
- backend/app/api/routers/gallery_collections.py
- backend/app/api/routers/gallery_queries.py
- backend/app/api/routers/gallery_tasks.py
- backend/app/api/routers/generate.py
- backend/app/api/routers/prompt.py
- backend/app/api/routers/prompt_snippets.py
- backend/app/api/routers/settings.py
- backend/app/repositories/db/execution.py
- backend/app/repositories/gallery/mutations.py
- backend/app/runtime/blocking.py
- backend/app/services/agent_context.py
- backend/app/services/agent_conversations.py
- backend/app/services/agent_run_context.py
- backend/app/services/agent_run_lifecycle.py
- backend/app/services/agent_stream.py
- backend/app/services/agent_tool_executor.py
- backend/app/services/assistant_batch.py
- backend/app/services/assistant_runtime.py
- backend/app/services/assistant_text.py
- backend/app/services/assistant_vision.py
- backend/app/services/dispatchers.py
- backend/app/services/edit_sources.py
- backend/app/services/gallery_common.py
- backend/app/services/gallery_export_jobs.py
- backend/app/services/gallery_import_jobs.py
- backend/app/services/gallery_job_shared.py
- backend/app/services/gallery_job_sse.py
- backend/app/services/gallery_jobs.py
- backend/app/services/gallery_maintenance.py
- backend/app/services/gallery_sync_jobs.py
- backend/app/services/image_job_aggregate.py
- backend/app/services/image_unit_execution.py
- backend/app/services/image_unit_session.py
- backend/app/services/job_cancel.py
- backend/app/services/job_events.py
- backend/app/services/job_queue.py
- backend/app/services/job_scheduler.py
- backend/app/services/nodeimage_upload_jobs.py
- backend/app/services/runtime_metrics.py
- backend/app/services/startup_maintenance.py
- backend/tests/test_architecture_boundaries.py
- backend/tests/test_blocking_executor.py
- backend/tests/test_db_execution.py
- backend/tests/test_performance.py
- backend/tests/test_performance_regressions.py
- backend/tests/test_sqlite_coordination_contract.py

## refactor(storage): split coordination persistence by domain

- backend/app/repositories/coordination.py
- backend/app/repositories/coordination/__init__.py
- backend/app/repositories/coordination/access.py
- backend/app/repositories/coordination/gallery_jobs.py
- backend/app/repositories/coordination/leases.py
- backend/app/repositories/coordination/sse_slots.py
- backend/app/repositories/coordination/workers.py
- backend/tests/test_access_settings_contract.py
- backend/tests/test_coordination_repository.py

## fix(frontend): guard generation job feed lifecycle

- frontend/src/lib/stores/jobs.ts
- frontend/src/lib/stores/jobsFeed.ts
- frontend/src/lib/stores/jobsState.ts
- frontend/tests/unit/jobsStore.test.ts

## test: make async provider fixture DNS deterministic

- backend/tests/conftest.py

## docs: record refactoring architecture contracts

- .trellis/spec/backend/db-execution.md
- .trellis/spec/backend/coordination.md
- .trellis/spec/frontend/jobs-lifecycle.md
- .trellis/spec/backend/index.md
- .trellis/spec/frontend/index.md
- .trellis/spec/backend/auth-and-security.md

## Preservation and bookkeeping

The existing backend/frontend spec indexes and auth guide were already untracked before this session. Their contents were preserved and only scoped links/owner paths updated; these six explicitly listed spec files are the only proposed files from that pre-existing spec tree.

After approved work commits, archive only the four tasks created for this refactoring and record this session through Trellis. Those task/archive/journal records have separate bookkeeping commits; do not archive Bootstrap Guidelines or stage its files.

Exclude existing .agents/skills/trellis-*/, .opencode/, .gitattributes, other .trellis scaffold/config/scripts/spec/workspace content and the separate Bootstrap Guidelines task. The generated Playwright report was preserved outside the repository in /private/tmp/gpt-image-linux-refactor-playwright-report.
