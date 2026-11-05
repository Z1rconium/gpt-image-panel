# Authorized split commit plan

User authorized split commits, push, merge into main and GitHub Actions Docker v1.9.0 publication on 2026-11-05.

## feat(backend): support GPT Image 2.6 markup and generated media

- `Dockerfile`
- `backend/app/api/body_limit.py`
- `backend/app/api/routers/edits.py`
- `backend/app/api/routers/gallery_queries.py`
- `backend/app/api/routers/generate.py`
- `backend/app/core/generated_media.py`
- `backend/app/core/image_cost.py`
- `backend/app/core/image_models.py`
- `backend/app/core/media.py`
- `backend/app/core/provider_mapping.py`
- `backend/app/core/svg_thumbnail.py`
- `backend/app/core/svg_validation.py`
- `backend/app/integrations/upstream/async_provider.py`
- `backend/app/integrations/upstream/contracts.py`
- `backend/app/integrations/upstream/generation.py`
- `backend/app/integrations/upstream/payloads.py`
- `backend/app/repositories/db/constants.py`
- `backend/app/repositories/db/migrations.py`
- `backend/app/repositories/image_files.py`
- `backend/app/repositories/thumbnails.py`
- `backend/app/schemas/assistant.py`
- `backend/app/schemas/gallery.py`
- `backend/app/schemas/generation.py`
- `backend/app/schemas/provider/_common.py`
- `backend/app/schemas/provider/resolved.py`
- `backend/app/schemas/provider/v2.py`
- `backend/app/services/edit_markup.py`
- `backend/app/services/edit_sources.py`
- `backend/app/services/gallery_archive_import.py`
- `backend/app/services/image_job_aggregate.py`
- `backend/app/services/image_unit_execution.py`
- `backend/app/services/job_queue.py`
- `backend/tests/test_access_settings_contract.py`
- `backend/tests/test_assistant_contract.py`
- `backend/tests/test_generated_media.py`
- `backend/tests/test_image_26_acceptance_contract.py`
- `backend/tests/test_image_26_options.py`
- `backend/tests/test_image_cost.py`
- `requirements.lock`
- `requirements.txt`

## feat(frontend): add GPT Image 2.6 controls and media workflows

- `frontend/src/lib/api/types/assistant.ts`
- `frontend/src/lib/api/types/gallery.ts`
- `frontend/src/lib/api/types/generation.ts`
- `frontend/src/lib/api/types/jobs.ts`
- `frontend/src/lib/api/types/settings.ts`
- `frontend/src/lib/components/GalleryGrid.svelte`
- `frontend/src/lib/components/Lightbox.svelte`
- `frontend/src/lib/components/PromptForm.svelte`
- `frontend/src/lib/features/agent/AgentComposer.svelte`
- `frontend/src/lib/features/workspace/Workspace.svelte`
- `frontend/src/lib/features/workspace/formState.svelte.ts`
- `frontend/src/lib/features/workspace/jobMaskRestore.ts`
- `frontend/src/lib/features/workspace/markup.ts`
- `frontend/src/lib/i18n/locales/en.ts`
- `frontend/src/lib/i18n/locales/zh-CN.ts`
- `frontend/src/lib/stores/editSource.ts`
- `frontend/src/lib/stores/preview.ts`
- `frontend/src/lib/utils/imageModels.ts`
- `frontend/src/lib/utils/promptForm.ts`
- `frontend/tests/e2e/edit-mask.spec.ts`
- `frontend/tests/e2e/image-26.spec.ts`
- `frontend/tests/e2e/responsive-a11y.spec.ts`
- `frontend/tests/unit/editSource.test.ts`
- `frontend/tests/unit/promptForm.test.ts`

## docs(images): document GPT Image 2.6 contracts and acceptance

- `.trellis/spec/backend/image-generation.md`
- `.trellis/spec/backend/index.md`
- `.trellis/spec/frontend/forms-and-api.md`
- `.trellis/tasks/11-05-gpt-image-26/acceptance.md`
- `.trellis/tasks/11-05-gpt-image-26/check.jsonl`
- `.trellis/tasks/11-05-gpt-image-26/commit-plan.md`
- `.trellis/tasks/11-05-gpt-image-26/design.md`
- `.trellis/tasks/11-05-gpt-image-26/implement.jsonl`
- `.trellis/tasks/11-05-gpt-image-26/implement.md`
- `.trellis/tasks/11-05-gpt-image-26/prd.md`
- `.trellis/tasks/11-05-gpt-image-26/research/official-api.md`
- `.trellis/tasks/11-05-gpt-image-26/task.json`

## chore(release): prepare v1.9.0

- `CHANGELOG.md`
- `README.md`
- `README.zh-CN.md`
- `README.zh-TW.md`
- `VERSION`
- `.env.example`
- `docker-compose.yml`
- `CONFIGURATION.md`

Pre-existing untracked infrastructure is excluded.
