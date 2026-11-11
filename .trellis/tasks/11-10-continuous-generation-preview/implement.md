# Execution plan

Status: implemented; verification complete.

- [x] Review the finalized plan at `plan/continuous-generation-preview.md` before activation.
- [x] Implement group/submission/slot state and race-focused unit tests.
- [x] Refactor tracked-job resources by jobId and verify lifecycle isolation against jobsStore tests.
- [x] Integrate Generate submissions and Workspace updates; preserve keyboard and edit paths.
- [x] Implement pending thumbnails and stable selection/metadata, including both locales.
- [x] Cover the acceptance matrix in the plan with unit and browser tests.
- [x] Run test:unit, check, build, check:bundle and check:worker-build in frontend.
- [x] Run prompt-submit.spec.ts and jobs-edit.spec.ts with Playwright; use scoped localhost proxy bypass and an isolated development port if required by the environment.
- [x] Update frontend/jobs-lifecycle.md to describe multi-job tracking and review the final change against R1–R5.

## What landed

| Area | File | Change |
| --- | --- | --- |
| Group model | `frontend/src/lib/stores/previewGroup.ts` (new) | Ordered submissions, stable slot ids, pending/success/failed/cancelled derivation, per-job event routing, pure and unit-tested. |
| Preview store | `frontend/src/lib/stores/preview.ts` | Appends submissions instead of replacing state; reserves slots before POST; attaches by submission id, routes by jobId; group epoch rejects late POST responses; generation starts a fresh group after an edit result. |
| Job tracking | `frontend/src/lib/stores/trackedJobs.ts` (new), `jobs.ts` | Per-jobId trackers with independent source/epoch/timer/poll token; global feed dispatches to every tracker; `untrackJob`/`closeActiveJobSource` release scopes. |
| Submit entry | `PromptForm.svelte` | `submitDisabled` (edits only) separated from `parameterControlsDisabled`; keyboard guard uses the same condition. |
| Workspace | `Workspace.svelte` | Applies jobs to their submission, seeds the group from the newest active job, opens history jobs as their own group, clear releases tracking then resets the group. |
| Presentation | `PreviewPanel.svelte`, `app.css`, locales | Unified slot list: streaming grid before any final image, then main image + horizontally scrollable strip with pending/failed/cancelled cards; metadata follows the selected image's job; `en` + `zh-CN` strings. |

## Verification results

- `npm --prefix frontend run test:unit` — 467 passed (28 files), including new `previewGroup.test.ts` and multi-job cases in `jobsStore.test.ts`.
- `npm --prefix frontend run check` — 0 errors, 0 warnings.
- `npm --prefix frontend run build` + `check:bundle` — homepage JS 114,701 B (≤ 122,880), main CSS 15,338 B (≤ 15,360), OGL 14,955 B.
- `npm --prefix frontend run check:worker-build` — production module worker verified.
- Playwright `prompt-submit.spec.ts` + `jobs-edit.spec.ts`: 28 passed, repeated three times; `phase-five`, `image-25`, `image-26`, `preferences`, `prompt-assistant`, `image-trace`, `responsive-a11y`, `i18n`: 62 passed; `gallery`: 35 passed; `edit-mask`: 55 passed.
- Pre-existing environment failures (reproduced on the unmodified baseline): `markup-editor` canvas pixel assertions differ under the locally installed Chrome channel, and the mask auto-fill frame-snap test is timing-flaky. Neither touches this change.

Risk boundaries: preview.ts, jobs.ts, Workspace.svelte and PreviewPanel.svelte must be reviewed together. Roll back submission enablement and state/tracker integration as a coherent change; no database rollback is needed.
