# Frontend cleanup validation

## Completed change boundary

The original behavior is retained; the batch removes duplicate implementation in existing owners:

- **F1** — `features/gallery/query.ts`: `gallerySearchBody` spreads the existing `galleryFiltersToSelectionPayload` in the original field position. Export names, property order, trimming, null flags, pagination/cursor/direction and include controls stay intact.
- **F2** — `utils/promptForm.ts`: edit conversion derives from gallery generation conversion, then explicitly overrides the active API path, edit-size fallback and chroma policy. Direct calls replace the private response-format pass-through. Gallery generation, job reuse/retry and prompt-only conversion remain separate entry points.
- **F3** — `components/PromptOptimizerAssistant.svelte`: pointer-up reuses `resetPointerState` after capture release. Pointer-ID guard, capture try/catch, click suppression and cancel-specific handling stay intact.
- **T1** — `scripts/bundle-budget.mjs`: only the uncalled script-local `isEntryNamed` declaration is deleted. Budget constants, graph traversal, manifest roots and reporting/exit logic are unchanged.

No templates, CSS, lazy imports, API contracts, lifecycle policy, dependencies or backend files changed in this batch.

## Characterization before production edits

`npm --prefix frontend run test:unit -- tests/unit/galleryQuery.test.ts tests/unit/promptForm.test.ts` passed **156 tests in 2 files** against the original implementations. The original gallery suite had 8 tests; the added characterization contributes 148 cases.

The new expectations cover exact empty/full request objects; whitespace and untrimmed non-prompt filters; nullable flags; collection/date filters; explicit pagination/cursor/direction/include controls; full/sparse converters; stored/current API path conflicts; dimension and `'-'` size fallbacks; both supported and unknown chroma modes; opaque/transparent backgrounds; zero/null/missing compression; response/output formats; quality including `xhigh` and `max`; model fallback; bounded and fractional quantity; job stream/partial-image/paste-back values; and prompt-only preservation.

Log: `/tmp/cleanup-frontend-characterization.log`.

## Candidate checks

- `npm --prefix frontend run test:unit`: **354 passed in 20 files**.
- `npm --prefix frontend run check`: **0 errors, 0 warnings**.
- `npm --prefix frontend run build`: passed.
- `npm --prefix frontend run check:bundle`: passed, unchanged budgets. Homepage JS **121,096 B gzip / 122,880 B**, CSS **14,222 B / 14,336 B**, OGL **15,159 B / 32,276 B**. Homepage JS is 70 B smaller than the planning baseline; CSS/OGL sizes are unchanged.
- `npm --prefix frontend run check:worker-build`: passed; production module Worker verified.
- `node --check frontend/scripts/bundle-budget.mjs`: passed.

Log: `/tmp/cleanup-frontend-candidate.log`. The first check attempt identified two types in the new test fixture (job status and overly broad shared parameter type); both were corrected before the successful candidate sequence. No product behavior changed to satisfy a check.

## Browser acceptance and actual renders

The sandbox denied the first Vite bind with `listen EPERM 127.0.0.1:5279`. Approved loopback-capable execution then ran this command from `frontend/`:

```sh
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PLAYWRIGHT_DEV_PORT=5279 \
npm run test:e2e -- tests/e2e/gallery.spec.ts tests/e2e/prompt-assistant.spec.ts tests/e2e/preferences.spec.ts \
--grep 'gallery edit choice|gallery cards can reuse|lightbox use all|filtered results through a batch token|floating prompt optimizer (opens|compares)|retry pins|gallery page input|gallery handles 500|lightbox navigates images across gallery pages|gallery url state' --workers=2
```

**12 Chromium cases passed**: gallery and lightbox edit entry, gallery prompt/full parameter reuse, lightbox use-all path fallback, cross-page lightbox navigation, URL-state restoration, page input navigation, 500-image cursor paging/filtering/selection, filtered batch token selection, job-preset retry, optimizer compare/reject/accept and optimizer click/long-press drag. The drag test now additionally proves that an unrelated pointer-up does not end dragging and that the matching release clears dragging without opening the dialog. Its existing second drag, post-release movement check, viewport clamping and reload-persistence checks passed unchanged.

Log: `/tmp/cleanup-frontend-browser.log`.

The two gallery cases were rerun after adjusting **capture-only** code to record real viewport contents rather than offscreen placeholders. `app.css` intentionally uses `content-visibility: auto` for gallery cards; full-page captures with the viewport at the form top can therefore show placeholders despite passing loaded-card behavior. Final captures scroll the complete card into view and wait for its image's `complete`/`naturalWidth`, and record the edit controls in a normal viewport to avoid fixed-header stitching. No product CSS or application scrolling policy was altered. Logs: `/tmp/cleanup-frontend-render.log` (both gallery cases pass), `/tmp/cleanup-frontend-workspace-render.log` (final edit viewport capture passes after waiting for dialog unmount/body-scroll release).

Actual screenshots inspected with `view_image`:

- `/tmp/cleanup-frontend-evidence/workspace-gallery-edit.png`: loaded edit parameters, including current Responses path, WebP, compression 80, auto background, quantity 2; the retained source image is also covered by the browser assertion.
- `/tmp/cleanup-frontend-evidence/gallery-filtered-page.png`: filter `500`, one loaded mock image, its title/metadata/actions and normal gallery controls.
- `/tmp/cleanup-frontend-evidence/optimizer-mobile-review.png`: readable original/optimized prompt sections and visible reject/accept controls at 390 × 844.

Mocks use the existing 1 × 1 transparent PNG, so the loaded gallery image shows the checkerboard media well rather than a photograph. The source assertions and inspected controls retain their existing meaning.

## Handoff

Ready for the main session's staged frontend/tooling commit and repository integration. Include the new `frontend/tests/unit/promptForm.test.ts` explicitly when staging; it was created by this batch. Frontend evidence remains valid for this product candidate; later edits only improve browser assertions and screenshot capture. No unresolved frontend acceptance failure remains. The isolated Playwright-owned servers exit with their runs; no user server was reused or stopped.
