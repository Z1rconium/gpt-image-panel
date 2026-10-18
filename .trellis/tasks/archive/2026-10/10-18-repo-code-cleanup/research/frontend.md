# Research: Frontend behavior-preserving cleanup

- Query: Which current frontend redundancies can be removed with a clear equivalence argument, while retaining supported UI behavior, entry points and lazy loading?
- Scope: internal; `frontend/src/**` and frontend test evidence. Build/deployment/tooling are owned by the main session.
- Date: 2026-10-18

## Findings

### Recommended batch: reuse existing implementations, do not add frameworks

Accept F1–F3 below as one small frontend batch. They eliminate existing duplicate statements/serialization, require no new abstraction or dependency, and leave public imports intact. They are cleanup opportunities, not demonstrated production defects. Do not rewrite large modules merely to reduce their line count.

### F1 — one filter serialization for search and batch-selection requests

**Evidence / files found**

- `frontend/src/lib/features/gallery/query.ts:55–73` builds the search JSON body. Its nine filter mappings at `:59–67` are identical to `galleryFiltersToSelectionPayload` at `:76–87`.
- Actual consumers: `frontend/src/lib/stores/gallery.ts:285,390` use `gallerySearchBody`; `:607` uses the selection payload when obtaining a filtered selection token.
- Existing focused tests: `frontend/tests/unit/galleryQuery.test.ts:56–71,90–105` cover prompt trimming, favorite and collection serialization, but not the complete shared shape.

**Change**

Keep both exported functions. In `gallerySearchBody`, replace the nine repeated fields with `...galleryFiltersToSelectionPayload(filters)`, in the same position after pagination fields. The existing helper remains the single definition of filter-to-wire mapping. No generic serializer, URL-format redesign or store split is needed.

**Preservation**

Keep exact keys and values: trimmed `prompt`; untrimmed model/preset/size/date strings; boolean-or-`null` favorite/mask flags; collection ID-or-`null`. Keep page/page-size numeric conversion, cursor `null`, default `direction: 'next'`, and include-flags exactly as now. This is JSON serialization only; URL state deliberately uses `collection` while the API uses `collection_id` and should not be folded into it.

**Validation**

Extend `galleryQuery.test.ts` with exact full-object expectations for both empty and populated filters, then assert that the filter portion of search equals the selection payload. Include whitespace prompt, false flags, date boundaries, collection ID, disabled count/filter-options and explicit cursor/direction. Run the gallery browser filtered-selection and paging cases after the batch.

### F2 — derive gallery edit defaults from the existing gallery parameter converter

**Evidence / files found**

- `frontend/src/lib/utils/promptForm.ts:68–87` (`galleryEntryToPromptForm`) and `:90–110` (`galleryEntryToEditForm`) repeat eleven identical field computations. Only API path, size fallback and chroma background policy differ.
- `normalizeJobResponseFormat` at `:33–35` simply returns `normalizeResponseFormat(value)`; its only callers are this file at `:61,83,106`.
- Actual application entry points in `frontend/src/lib/features/workspace/Workspace.svelte`: edit uses the edit converter at `:911`; gallery “use all” uses the gallery converter at `:1144`; job reuse/retry uses the job converter at `:1151,1190`; prompt-only reuse remains separate at `:1136`.
- Existing browser contract: `frontend/tests/e2e/gallery.spec.ts:71` explicitly requires gallery edit to preserve the current API path, `:520` covers prompt/full parameter reuse, and `:544` covers lightbox “use all”. There is no dedicated unit test for these converters in the current 19-file unit suite.

**Change**

Implement the edit converter as the existing `galleryEntryToPromptForm(image, fallbackModel, currentApiPath)` result followed by the three explicit edit overrides. Keep its signature/export and local size computation. Delete the private pass-through response-format wrapper and use `normalizeResponseFormat` directly at its three call sites.

**Preservation**

- Edit always keeps `currentApiPath`, even if the image has a different valid generation path.
- Edit size remains `image.size || displayImageSize(image)`, falling back to initial size only for `'-'`; do not substitute the generation converter's ordinary size fallback.
- Every `chroma_*` background becomes `'auto'` for editing, including unrecognized `chroma_` values; other backgrounds retain current normalization.
- Model fallback, quantity clamping, zero-vs-missing compression, quality/output/response-format normalization and initial stream/partial-image/paste-back values remain unchanged.
- Keep `jobToPromptForm` and `galleryEntryToPromptOnly` separate. Job stream/paste-back values and prompt-only preservation are different contracts, not duplicate defaults to erase.

**Validation**

Before refactoring add `frontend/tests/unit/promptForm.test.ts` with exact snapshots for all three public converters and prompt-only reuse. Cover full data; sparse/legacy values; conflicting stored/current API paths; width/height-derived size; missing dimensions; both chroma modes and unknown `chroma_*`; transparent/opaque; compression `0`/`null`/missing; valid and invalid response formats; quality `xhigh`/`max`/invalid; model fallback; quantity default/lower/upper bounds; job-specific streaming/partial-images/paste-back. Do not change odd-but-current quantity semantics such as fractional values under a cleanup label. Reuse existing gallery-edit, parameter-reuse and retry browser tests.

### F3 — reuse pointer reset already owned by the optimizer component

**Evidence / files found**

- `frontend/src/lib/components/PromptOptimizerAssistant.svelte:167–176` defines `resetPointerState`.
- `handlePointerUp` at `:344–363` repeats that function's exact eight statements at `:355–362` after releasing pointer capture.
- Other current callers are pointer-cancel `:367` and unmount cleanup `:402,408`; the reset deliberately does **not** alter `suppressClick`.
- Existing browser evidence is unusually direct: `frontend/tests/e2e/prompt-assistant.spec.ts:178–281` exercises click, long-press drag, release, no following movement and persisted position after reload.

**Change / preservation**

Replace only the repeated eight-statement block with `resetPointerState()`. Keep active-pointer guard, pointer-capture release ordering/try-catch, and `suppressClick` handling unchanged. Do not merge pointer-cancel and pointer-up: cancel additionally clears click suppression.

**Validation**

Run the existing floating-optimizer drag case and compare/reject/accept case. Extend the drag case only if needed to exercise a second drag and a mismatched-pointer release; no new component harness is needed for this statement-level reuse.

## Reviewed coverage versus sampling

### Inventory and mechanical scan

The current source inventory contains **146 files / 31,273 lines** under `frontend/src` (TS, Svelte, CSS and HTML). Every source file was included in a local import-reference/reachability scan, exported-symbol reference count scan, and exact seven-line script-block duplicate scan. The import heuristic includes static imports, literal dynamic imports, route entry files and `new URL('./maskRegion.worker.ts', import.meta.url)` worker references. It found **no unreachable source file candidate**. No exported function was proven unused. Framework exports `ssr`/`prerender` are live by SvelteKit convention, not textual callers.

This is whole-tree structural coverage, **not** a line-by-line semantic review of all 31,273 lines. Large visual components, locale catalogs and geometry algorithms were sampled. The accepted changes above have direct source and caller review; no deletion is justified from a low symbol-count heuristic alone.

### Source areas and depth

| Area | Reviewed evidence | Depth / conclusion |
|---|---|---|
| Entry points | `routes/+page.svelte`, `+layout.svelte`, `+layout.ts` | Full source; page delegates to Workspace; layout gates initial i18n only, initializes theme; static client-only app contract stays. |
| API client/events | `lib/api/client.ts`, `events.ts`, `agent.ts` | Full source; JSON/raw timeout/error contracts intentionally differ. Raw helper from prior audit is already implemented. |
| API types | all nine `lib/api/types/*.ts` in the reference scan; common/gallery/jobs directly read | Structural/type-shape review. Similar DTO fields alone do not justify changing public shapes. |
| Stores | all 28 `lib/stores/*.ts` in mechanical scan; full small stores and jobs feed/state read; sampled gallery, deferred actions, assistant, agent/state, edit source, preview, collections | No dead module. Async sequence ownership, optimistic deletion and operation counters are retained; confirmed duplication is isolated in pure converters, not lifecycle machinery. |
| Workspace helpers | gallery, lifecycle, panelController, panels, prompt, jobMaskRestore fully read; lightbox fully/targeted read; form controller read | Lazy boundaries, focus restoration, URL and prefetch ownership retained. Workspace wiring/callers sampled, not full 1,749-line semantic review. |
| Components | all 43 `lib/components/**/*.svelte` have script/import/function and exact-block scan; optimizer reset and settings draft/health excerpts directly read | F3 is exact reuse. Gallery/lightbox/mask/other panels sampled; no mass template or CSS rewrite proposed. |
| Agent feature | all nine component scripts indexed; agent state/stream, block/markdown/mention helpers directly read | Stream epochs, per-frame batching, Unicode citation offsets and safe markdown retain their purpose. |
| Gallery feature | both modules directly read, query tests directly read | F1 accepted; drag/swipe geometry remains domain-specific. |
| Mask feature | six modules in graph/duplicate scan; worker/client and layer/crop helpers directly read; document/snap contracts and representative algorithms sampled | Worker fallback, binary mirrors, bounded queues, protected pixels, natural-resolution export and idempotence are real requirements. Geometry is not a cleanup target for size alone. |
| Settings feature | `features/settings/draft.ts` full source, existing tests indexed | Secret masking vs env/registry references and stable provider-config comparison are purposeful. Avoid collapsing their distinct policies. |
| Shared utilities | all 16 `lib/utils/*.ts` directly read; `motion.ts`, actions and virtualization directly read | F2 accepted. Date formatter cache, failed chunk reload handling, cross-tab notification dedupe, clipboard fallback and virtual-window binary search retain observable/performance contracts. |
| i18n | loader full source; types/catalogs in structural scan | English eager/Chinese lazy, latest-load ownership and keeping mounted workspace during language switches remain. Catalog content is not semantically audited key-by-key. |
| WebGL / styles | support/adapter and exposure setup/cleanup sampled; app/theme CSS and app HTML included in structural inventory | Progressive exposure, lazy OGL import, reduced-motion and context cleanup stay; no visual/style refactor proposed. |
| Tests | complete test file inventory; galleryQuery and relevant gallery/prompt-assistant/preferences cases read; jobsStore suite and other suites indexed | No test deletion proposed. Root supplies current passing baseline; browser tests listed below are the acceptance checks for this batch. |

## Retained complexity and rejected directions

1. **No renewed F5/F6 module splits.** The archived frontend scan and `refactor-checklist.md` explicitly defer splitting `galleryDeferredActions.ts` and Workspace. Current evidence adds no responsibility defect that warrants those splits. Lazy `galleryActions.ts` forwarding keeps heavy gallery operations out of eager chunks; replacing it with an eager import would change loading/performance.
2. **Old G11 raw-fetch finding is resolved.** `apiFetchRaw` exists at `api/client.ts:117`, and `jobMaskRestore.ts:22`/deferred gallery downloads use it. Do not re-implement an already landed helper or make raw streaming errors consume the body.
3. **Lifecycle guards are not redundant.** `jobsFeed.ts:39–49,57–62,76–82,105–109` distinguish request sequence, accepted snapshot revision, source epoch and polling token. `jobs.ts:149–168` already fences history clearing; predecessor F8 calls for regression evidence, not another refactor. Agent request/source epochs serve related but different per-turn contracts.
4. **Similar lightbox AI functions have different writes.** `workspace/lightbox.ts:241–287`: describe preserves prior prompt/analysis, analyze replaces them and calls `onAnalyzed`. Do not use a boolean-configured generic async wrapper merely to hide that distinction.
5. **Runtime fallbacks stay.** Clipboard fallback, missing Worker/OffscreenCanvas, missing metadata on legacy images, multipart timeout policy and transient-network behavior all represent supported conditions. SHA-256 in `jobMaskRestore.ts:15–16` is required source-image identity verification, not dispensable bookkeeping.
6. **One optional micro-optimization is not part of the recommended batch.** `maskRegion.worker.ts:39,50` crops the same output twice on the no-bitmap fallback; `maskRegion.ts:540–546,554–592` show cropping is pure and additions-layer creation only reads its input. Reusing one cropped buffer is valid, but its benefit is small and it would open a worker-specific test surface unrelated to the selected frontend batch. Retain unless the parent chooses a worker-focused cleanup.
7. Several frontend specs are still template placeholders (component/directory/state/type/quality). Apply actual repository contracts in `forms-and-api.md` and `jobs-lifecycle.md`; do not treat placeholder headings as mandates for cosmetic “modernization”. Existing Svelte event syntax that compiles is not proven dead or incorrect.

## Focused validation commands

Run from `frontend/` after implementation, reusing the main session's unchanged baseline rather than rerunning it during this research:

```sh
npm run test:unit -- tests/unit/galleryQuery.test.ts tests/unit/promptForm.test.ts
npm run check
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PLAYWRIGHT_DEV_PORT=<free-port> npm run test:e2e -- tests/e2e/gallery.spec.ts tests/e2e/prompt-assistant.spec.ts tests/e2e/preferences.spec.ts --grep 'gallery edit choice|gallery cards can reuse|lightbox use all|filtered results through a batch token|floating prompt optimizer (opens|compares)|retry pins'
```

At final integration use the existing `npm run test:unit`, build, bundle-budget and worker-build checks because F2 is imported on the initial workspace path. The root reported the pre-change baseline as `check` 0 errors / 0 warnings, **206 unit tests across 19 files**, build/budget/Worker green, and installed Chromium; these are parent-provided results, not rerun by this research owner.

## Related specs and prior sources

- `.trellis/spec/frontend/index.md` — documentation language is English.
- `.trellis/spec/frontend/forms-and-api.md` — shared API client, raw response semantics, store-owned remote state.
- `.trellis/spec/frontend/jobs-lifecycle.md` — ownership guards, disposal/reuse and test environment settings.
- `.trellis/tasks/archive/2026-10/10-15-backend-review-refactor/research/g11-frontend-quickscan.md` — prior quick scan, deferred F5/F6 and F8.
- `.trellis/tasks/archive/2026-10/10-15-backend-review-refactor/refactor-checklist.md` — old recommendations and landed/deferred status.
- `.trellis/tasks/archive/2026-10/10-13-architecture-legacy-refactor/checklist.md` — prior integration acceptance/evidence.
- `frontend/package.json` — current local declarations: Svelte `^5.38.0`, SvelteKit `^2.70.2`, TypeScript `^5.9.2`, Vite `^6.4.3`, Vitest `^4.1.11`, Playwright `^1.60.0`. No dependency change is proposed.

## Caveats / Not Found

- No external references or network calls were needed; all conclusions are based on checked-out source, repository specs and archived audits.
- No product files were edited, no tests were executed by this owner, and no task lifecycle or Git operation was performed.
- A module being reachable is not proof every branch is used; no branch deletion is justified solely from this structural scan.
- F1 and F3 already have focused test entry points. F2 requires the new converter characterization test before editing; existing browser tests alone do not enumerate its normalization edge cases.
