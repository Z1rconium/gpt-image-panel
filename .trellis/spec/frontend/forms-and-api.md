# Frontend Forms and API Calls

## Always call `apiFetch`

All HTTP goes through `apiFetch<T>` in `frontend/src/lib/api/client.ts`. It sets
`credentials: 'same-origin'`, applies a default 30s timeout, parses JSON, and throws
`ApiError` with `.status` on failure. A `401` also invokes the registered
unauthorized handler (`setUnauthorizedHandler`).

```ts
const settings = await apiFetch<SettingsResponse>(
  '/api/settings',
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  },
  'saving settings'          // action label used in error messages
);
```

Do not call `fetch` directly for application APIs.

For responses that must be read raw — binary bodies, response headers, or a
streamed body (downloads with progress, Save File Picker writes) — use
`apiFetchRaw` from the same module. It returns the `Response`, applies the same
`credentials: 'same-origin'` default as `apiFetch`, and accepts `timeoutMs`
(mapped to an `AbortSignal.timeout`); a caller-provided `signal` wins and, with
neither, no timeout is applied (large downloads may legitimately run long).
Error mapping and `401` handling stay with the caller because consuming the body
to build an `ApiError` would break the stream.

## Where calls belong

- Request/response types: `frontend/src/lib/api/types/*.ts`.
- Remote state + actions: a `writable` store in `frontend/src/lib/stores/*.ts`. The
  store owns the `apiFetch` call and exposes async actions. See
  `frontend/src/lib/stores/settings.ts`, `saveSettings` (validates, sets a saving
  flag, calls `apiFetch`, updates state, shows a toast, clears the flag in `finally`).
- Components call store actions; they do not embed request details (urllib paths,
  headers, JSON stringify) inline.

## Forms

Use a real `<form>` with an `onsubmit` handler that calls `event.preventDefault()`,
`bind:value` for inputs, and `bind:checked` for toggles:

```svelte
<form onsubmit={(event) => { event.preventDefault(); void saveEdit(); }}>
  <input bind:value={apiKey} type="password" autocomplete="off" spellcheck="false" />
</form>
```

Examples: `frontend/src/lib/features/agent/AgentComposer.svelte`,
`AgentMessage.svelte`, and the sections in
`frontend/src/lib/components/settings/` (`bind:value`, `bind:checked`).

## Reusing image parameters

Use the converters in `lib/utils/promptForm.ts` when a gallery image or job
populates the workspace form:

```ts
const reused = galleryEntryToPromptForm(image, fallbackModel, currentApiPath);
const edited = galleryEntryToEditForm(image, fallbackModel, currentApiPath);
```

Full gallery reuse accepts a valid stored generation API path. Editing keeps
`currentApiPath`, derives missing size from image dimensions, and turns every
`chroma_*` background into `'auto'`. These three edit overrides stay explicit
when shared fields reuse the gallery converter. Prompt-only reuse changes only
the prompt; job reuse retains its own stream, partial-image and paste-back values.

Preserve the existing normalization edge cases: compression `0` becomes `'0'`,
missing compression becomes `''`, and quantity conversion clamps without adding
integer rounding. Freeze converter behavior in `tests/unit/promptForm.test.ts`
before refactoring; gallery edit, parameter reuse and retry browser cases cover
the consumer workflows.

### Model capabilities travel with reused parameters

Keep `complexity`, `frame_count` and `frame_duration_ms` in both API types and
form converters; omitted values stay null/empty rather than becoming explicit
upstream defaults. The exact 2.6 model IDs enable SVG/GIF only when generating.
Switching to editing, a model or provider that cannot send a selected option
resets incompatible controls with localized feedback. Provider `markup`,
`complexity` and `animation` capabilities must gate their controls, and the
backend remains the authority for the actual operation's mapping.

The uploaded markup layer belongs to the primary edit source, separately from
its mask. Validate transparent PNG bytes and upright dimensions under both the
16M-pixel limit and deployment byte cap. Replace/remove it explicitly, release
object URLs, and clear it when the primary changes (not when adding references).
Retry restoration goes through the identity-checked POST endpoint; a same-sized
but different source must not inherit an annotation. Never enable mask paste-back
for markup.

#### In-browser markup editor contract

The workspace draws markup in `MarkupEditorDialog` (lazy `markupEditor` panel;
`features/markup/markupDocument.ts` + `markupViewport.ts`). A mask editor is not
reused: a mask means "pixels that may change" and is binarised, while markup
keeps colour, text and antialiased alpha.

- **Layer and coordinates.** One native-resolution RGBA layer, transparent at
  start; the primary image is rendered separately and is never drawn into it.
  Commands are recorded in image pixels and preview/export share
  `renderMarkupCommand`. Zoom, pan, window size and DPR change only the viewport
  canvas (sized from the stage and DPR, capped), never the exported PNG size.
- **Primary image identity.** Dimensions and display pixels come from
  `features/workspace/editPrimaryImage.ts`: the upload `File`, or the
  authenticated same-origin `/api/image/<filename>` for gallery sources — never
  `galleryPreviewUrl` (a CDN preview) and never gallery size metadata (raw,
  pre-EXIF). `attachMarkup` and the editor both use it. Decode with default
  orientation handling so EXIF photos are upright.
- **Working copy.** Opening, drawing and clearing never touch the applied
  `editSourceStore.markup`; cancel/close with unapplied changes asks first. Reopen
  continues from `markup.editorState` (immutable `{width,height,base,commands}`,
  browser-only, type-only import in `editSource.ts`, never uploaded). Uploaded
  (`origin: 'uploaded'`) and task-restored (`'restored'`) PNGs open as an
  un-undoable base layer. Undo keeps 100 steps; older commands are folded into a
  base checkpoint and the visible result is kept. The editable state is not
  persisted across reloads and no image data goes to storage or logs.
- **Apply is atomic.** Export (fails with explicit `empty`/`opaque`/`encode`/
  `canvas` reasons), `validatePaintedMarkup`, mask-replacement confirmation, then
  `installMarkup` — a synchronous check-and-swap that re-verifies the primary id
  and the caller's session. Every await is followed by a `current()` re-check
  (session id, open state, capability, primary id, applied-markup version). A
  refusal leaves the previous markup, the mask and the draft untouched; the old
  object URL is revoked only by the swap.
- **Session invalidation.** The editor session ends (editor closed, canvases and
  bitmaps released, late results refused) when the primary image changes or is
  removed, or the model/provider stops supporting markup. Adding/removing
  reference images does not. Applying a mask over a markup is confirmed the same
  way as the reverse.
- **Input.** Pointer Events with capture; coalesced events + `requestAnimationFrame`.
  A second touch cancels an unfinished stroke and becomes a pinch; `pointercancel`,
  capture loss and window blur cancel instead of committing a partial shape;
  nothing is drawn after a pinch until all fingers lift. Escape closes the text
  box first, then cancels an in-progress gesture, then the dialog. IME composition
  Enter never submits text.
- **Budgets.** Drawing code stays in the lazy chunk (the store only imports
  types); reuse existing utility classes before adding new ones — the main CSS
  gzip budget is nearly exhausted.
- **Tests.** `tests/unit/markup{Viewport,Document,Install}.test.ts`,
  `editSource.test.ts` and `tests/e2e/markup-editor.spec.ts` (real canvas pixels,
  multipart bodies, touch via CDP, lifecycle and resource tracking).

Render SVG/GIF originals using an `img` URL. Keep animation and download bytes;
never inject SVG as HTML. Disable raster-only edit/reference actions for these
new outputs at every entry point, while retaining existing custom-model input
behavior. Unit tests cover converters and bound-layer lifecycle; browser tests
must exercise upload/from-gallery/retry, actual loaded previews and original
downloads, including a narrow mobile viewport. Add UI copy to both existing
locale catalogs (`en`, `zh-CN`); the three README translations are separate docs.

### Gallery filters share one wire mapping

`gallerySearchBody` reuses `galleryFiltersToSelectionPayload` from
`lib/features/gallery/query.ts`. Keep the prompt trim, `date_from`/`date_to`,
`collection_id`, and true-or-null favorite/mask flags identical for search and
batch selection. Pagination, cursor/direction and include flags remain search
controls. URL-state serialization uses its own URL keys and stays separate.
`tests/unit/galleryQuery.test.ts` checks complete empty and populated payloads.

## Toasts and i18n

- Toast UI is in `frontend/src/lib/stores/ui.ts`: `uiStore.showToast(message, variant?,
  options?)` where `variant` is `'status' | 'error'`.
- Never hard-code user-facing text; read it via `t` from `$lib/i18n`
  (`{$t.settings.nodeImageApiKey}`).

## Anti-patterns

- Calling `fetch` directly instead of `apiFetch`.
- Hard-coding English strings instead of using the i18n catalog.
- Mixing request construction into a `.svelte` template instead of a store action.
