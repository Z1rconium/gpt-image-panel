# Generation-job lifecycle validation

## Implementation and regression evidence

- jobsState.ts owns the existing equality/list/set/history helpers and state shape. Independent comparison against HEAD confirms all seven extracted helper signatures and bodies are byte-identical after removing the export prefix.
- jobsFeed.ts owns global SSE/polling/visibility and guards each source callback with source identity and lifecycle epoch. List responses also require current request sequence and unchanged accepted-event revision. Poll finally blocks check their own token.
- jobs.ts retains the public facade, tracked preview, notifications, selections and history; load/start/track can explicitly reactivate resources following cleanup.
- Implementer reported nine lifecycle regressions failing against the original module and thirteen passing after extraction.
- Reviewer added a fourteenth focused case: an accepted single-job terminal event invalidates an outstanding list snapshot, so its eventual older status produces no list, preview or notification effects.

## Independent Trellis review

- Checked all global open/event/network-error/error callbacks, source replacement and disposal epochs, HTTP request/event ordering, poll token ownership, timer teardown and listener reinstall.
- Checked visible/hidden polling and repeated cleanup/reuse; verified Workspace awaits its initial list before starting the global stream.
- Checked notification observation and selected-ID pruning remain in accepted store callbacks; tracked stream generation and history request guards remain unchanged.
- Reviewed .trellis/spec/frontend/jobs-lifecycle.md against actual controller signatures and ownership; no contract drift found.
- No semantic production-code fixes were required during review. Added the focused regression above; a later staged whitespace check identified and removed one redundant EOF blank line in jobsState.ts.

## Verification

Independent reviewer runs after the extra regression:

| Check | Result |
|---|---|
| Dedicated lint command | Not configured in frontend/package.json |
| git diff --check -- frontend | Pass for tracked changes; the later staged check additionally exposed a previously untracked EOF blank line, corrected with a one-newline-only working-tree change |
| npm --prefix frontend run test:unit -- jobsStore | 14 passed |
| npm --prefix frontend run test:unit | 19 files, 206 tests passed |
| npm --prefix frontend run check | 0 errors, 0 warnings |
| npm --prefix frontend run build | Pass |
| npm --prefix frontend run check:bundle | Pass; homepage JS 121,105 B gzip / 122,880 B, CSS 14,222 B / 14,336 B |
| npm --prefix frontend run check:worker-build | Pass; production module Worker verified |

Implementation-stage focused browser run: jobs-edit, prompt-submit and phase-five completed with 29 passed and 3 existing opt-in performance skips. Environment used scoped NO_PROXY/no_proxy=127.0.0.1,localhost, PLAYWRIGHT_DEV_PORT=5187 and CI=true with loopback permission. Reviewer did not repeat browser checks after a unit-test-only addition; no production/config/browser-test changes followed that run.

## Deferred existing behavior

History cleanup can leave historyLoading true: loadJobHistory sets it before awaiting (jobs.ts:72), cleanup increments historyRequestSeq (jobs.ts:379), and the old finally clears loading only when the sequence still matches (jobs.ts:117). Subsequent history loads then return at their historyLoading guard. The same logic existed before this refactor. This is a history-lifetime behavior change outside the approved history-preservation scope; it remains documented for a separately scoped fix rather than silently changing history here.
