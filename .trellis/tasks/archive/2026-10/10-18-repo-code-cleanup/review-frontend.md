# Independent acceptance: frontend and tooling batch

## Decision

**Accepted for the staged frontend/tooling commit.** F1/F2/F3/T1 preserve the inspected contracts. The tests characterize the original implementation rather than merely comparing one shared helper with itself. No product/test correction or specification change was necessary during this review.

Reviewed the complete frontend product/test diff against `12a19db`, the new `frontend/tests/unit/promptForm.test.ts`, `validation-frontend.md`, and the frontend forms/API specification and index updates. Backend B4 and Git operations are outside this review's ownership.

## Findings (fixed)

None in product code. Initial full-page screenshot artifacts showed offscreen gallery placeholders. This was returned to the original owner, who corrected only capture synchronization and viewport positioning, accounting for existing `content-visibility: auto` and dialog scroll-lock release. The final capture changes and successful reruns were inspected; no application behavior or styling was changed to satisfy visual acceptance.

## Findings (not fixed)

None. No source, test or documentation issue remains for this batch.

## Verified contracts

- **F1 — identical wire payload and order:** `frontend/src/lib/features/gallery/query.ts:55–79` inserts the existing serializer exactly where the original nine fields appeared. Its property insertion order and values match the original statements, preserving the `JSON.stringify` request/cache key used at `frontend/src/lib/stores/gallery.ts:285–286`. Prompt trimming, other strings, true-or-null flags, collection nullability, numeric coercion, cursor/direction and include controls are unchanged. Search and selection retain separate entry points; selection's enclosing `filters` payload at `gallery.ts:600–608` and URL serialization are unchanged. New tests use literal complete expected empty/populated objects before the additional cross-helper comparison.
- **F2 — three deliberate edit overrides:** `frontend/src/lib/utils/promptForm.ts:86–97` retains the active API path, the original `image.size || displayImageSize(image)` computation and `'-'` fallback, and the broad `chroma_*`-to-auto editing policy. The shared converter at `:64–83` supplies the same remaining fields as the original edit function. Object spreading followed by replacement of existing keys preserves the original form key order. Public signatures/exports and defaults remain intact; only a private response-format pass-through was removed. Job streaming/nullish partial-image and paste-back semantics at `:46–61` and prompt-only reuse at `:100–105` remain distinct. Actual edit/use-all/retry callers in `Workspace.svelte` retain their paths and arguments.
- **F2 characterization:** new tests have independent literal full-form expectations, sparse expected defaults with the existing empty response-format result, and explicit normalization tables. They cover conflicting generation/edit API paths, dimension fallback, both known and unknown chroma values, compression zero/null/missing, quality and formats, model fallback, bounded and fractional quantity, job-specific values and source non-mutation. Their expectations agree with the actual `12a19db` converter and retained initial state; no behavior was redefined to make the candidate pass.
- **F3 — same pointer state transition:** `frontend/src/lib/components/PromptOptimizerAssistant.svelte:344–355` retains the pointer-ID guard and capture release/try-catch before invoking the exact existing reset statements at `:167–176`. Reset does not clear click suppression. Cancel still clears it separately at `:358–361`, and click still consumes suppression at `:364–371`. Existing drag/re-drag, post-release stability and persistence checks remain; the added unrelated pointer-up and matching-release assertions directly exercise the changed path.
- **T1 — deletion only:** comparison against `12a19db` shows only removal of the non-exported, uncalled `isEntryNamed` declaration from `frontend/scripts/bundle-budget.mjs`. No budget, graph traversal, manifest root, report or exit semantics changed. No remaining code consumer references the deleted predicate or response-format wrapper.
- **Specification:** `.trellis/spec/frontend/forms-and-api.md:58–87` accurately describes current converter differences, quantity/compression handling and true-or-null search/selection flags; its index entry resolves correctly. No further synchronization is needed.

## Verification

Evidence was read from `validation-frontend.md` and its actual logs; valid checks were not rerun for the unchanged product candidate:

- **Tests — pass:** `/tmp/cleanup-frontend-characterization.log` records 156 cases in two files on the original implementation; `/tmp/cleanup-frontend-candidate.log` records 354 candidate unit tests in 20 files.
- **TypeCheck/Svelte diagnostics — pass:** candidate log records zero errors and zero warnings. The frontend has no separate lint script; its established diagnostic check passes.
- **Build/budget/Worker — pass:** candidate log confirms production build and Worker verification, unchanged budgets, homepage JS 121,096 B gzip / 122,880 B, CSS 14,222 B / 14,336 B and OGL 15,159 B / 32,276 B. Script syntax check passed in the same sequence.
- **Browser — pass:** `/tmp/cleanup-frontend-browser.log` records all 12 targeted Chromium cases passing, including editing, reuse/retry, filtered batch selection, cursor/page/URL navigation and optimizer compare/accept/drag. `/tmp/cleanup-frontend-render.log` records both capture cases passing after viewport capture adjustments; `/tmp/cleanup-frontend-workspace-render.log` records the final workspace capture case passing after dialog unmount synchronization. Browser logs' color-environment warnings do not affect results.
- **Diff hygiene — pass:** independently ran `git diff --check --` on all assigned tracked product, unit/browser test and specification paths.

## Actual render inspection

Independently opened all three final images with `view_image`:

- `/tmp/cleanup-frontend-evidence/workspace-gallery-edit.png`: visible, loaded edit controls retain model, 1024x1024 size, high quality, WebP, compression 80, auto background, quantity 2, current Responses API path and URL response format.
- `/tmp/cleanup-frontend-evidence/gallery-filtered-page.png`: `500` filter and the complete loaded `Paged gallery image 500` card, metadata and action buttons are visible. The checkerboard is the established transparent 1×1 mock image, not an unloaded placeholder; the added image completion assertion checks that distinction.
- `/tmp/cleanup-frontend-evidence/optimizer-mobile-review.png`: existing mobile test's original/optimized prompt sections and reject/accept controls are readable and contained in the dialog.

The original capture-only gap is resolved. No render-inspection dependency remains. Changes are proportionate: deletion/reuse in existing owners, direct boundary-value tests and small additions to existing browser flows; no new framework, viewport matrix, UI redesign or runtime fallback was introduced.

## Next owner

The main session can stage/commit/push this batch with its new unit test and evidence/specification updates, then finish integrated acceptance with the remaining backend batch. The frontend product checks above remain valid unless its product source changes.
