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
