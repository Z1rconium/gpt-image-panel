# G11 frontend quick scan — baseline a50da7a

## Size / structure map (top modules)
| Module | Lines | Note |
|---|---|---|
| lib/components/MaskEditorDialog.svelte | 2068 | mask editor UI (geometry in maskDocument/maskRegion) |
| lib/features/mask/maskDocument.ts | 1913 | mask document model |
| lib/features/workspace/Workspace.svelte | 1749 | **F6 (deferred)** |
| lib/i18n/locales/en.ts / zh-CN.ts | 1089 / 1088 | locale tables |
| lib/stores/galleryDeferredActions.ts | 1043 | **F5 (deferred)** |
| lib/stores/gallery.ts | 776 | gallery store |
| lib/components/Lightbox.svelte / GalleryGrid.svelte | 753 / 726 |  |
| lib/features/mask/maskRegion.ts | 719 | mask geometry |
| lib/components/SettingsDrawer.svelte | 683 |  |
| lib/stores/agent.ts | 535 |  |

Organization: `lib/stores`, `lib/features/<domain>`, `lib/components`, `lib/api` — consistent convention, no drift found at quick-scan depth.

## Contract adherence
- `forms-and-api.md` (`apiFetch` for all HTTP): **3 raw `fetch` call sites** bypass it — see F-G11-1.
- `jobs-lifecycle.md`: `stores/jobs.ts` (404) + `jobsFeed.ts` (132) + `jobsState.ts` (117) match the documented facade/feed/state split.

## Deferred items status (F5/F6/F8)
- **F5** `galleryDeferredActions.ts` (1043): unchanged single factory covering NodeImage upload / batch / archive / streaming / R2. Remains deferred as planned.
- **F6** `Workspace.svelte` (1749): unchanged. Remains deferred. MaskEditorDialog (2068) / maskDocument (1913) remain large; predecessor guidance (do not rewrite geometry without browser coverage) still applies.
- **F8** history cleanup: **appears addressed**. `jobs.ts` guards every history mutation on `historyRequestSeq` and clears `historyLoading` only for the owning request (`:86`, `:108`, `:117`), including `clearJobHistory` (`:149`, `:157`). No frontend test references `historyLoading` (`grep frontend/tests` empty) → recommend a targeted regression test rather than a refactor.

## Platform facts
- Svelte **5.38** with runes (`$state`/`$props`/`$derived`) used in 28 files; SvelteKit **2.70**; Vite **6.4**; TypeScript **5.9**. No deprecated Svelte 4 API dependency found at quick-scan depth.

## Findings

### F-G11-1 — P3, improvement: api client has no binary/streaming variant, so 3 sites re-implement HTTP conventions
- Evidence: raw `fetch(` at `frontend/src/lib/features/workspace/jobMaskRestore.ts:22` (blob + `X-Mask-Coverage` header, manual credentials/timeout/error mapping) and `frontend/src/lib/stores/galleryDeferredActions.ts:756` (download), `:810` (batch download). The client's own `api/client.ts:56` is the only other `fetch`.
- Justified in each case (binary body / response headers / streaming), but credentials/timeout/`ApiError` mapping are re-implemented per site, which is exactly what `forms-and-api.md` centralizes.
- Remedy sketch: add `apiFetchRaw`/`apiFetchBlob` to `lib/api/client.ts` (same credentials, timeout, unauthorized-handler, error mapping) and route the 3 sites through it.
- Effort/risk: low / low.

## Coverage notes
- Quick scan only: no per-file logic review of mask geometry, i18n, or panel controllers per the agreed scope.
- No P1/P2 frontend findings this round.
