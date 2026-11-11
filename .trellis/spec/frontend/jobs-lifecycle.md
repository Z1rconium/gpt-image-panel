# Global generation-job feed lifecycle

## 1. Scope / Trigger

Use when changing global generation-job SSE, fallback polling, per-job tracking or jobsStore lifetime. State/equality/history helpers live in stores/jobsState.ts; global feed resources live in stores/jobsFeed.ts; per-job streams/polling live in stores/trackedJobs.ts; stores/jobs.ts remains the public writable-store facade and retains tracked-preview/history behavior. Preview aggregation (ordered submissions, stable slots) lives in stores/previewGroup.ts behind stores/preview.ts.

## 2. Signatures

```typescript
createJobsFeedController({
  onSnapshot: (jobs: GenerateJobStatus[]) => void,
  onJob: (job: GenerateJobStatus) => void,
  onVisible: () => void
});
// Returns activate(), load(), startEvents(), dispose(), healthy getter.
```

createTrackedJobsController();
// Returns track(jobId, {onJob, onError, onPreview}), release(jobId),
// releaseAll(), applyFromFeed(job), resumePolling(), size, healthy.

jobsStore preserves its existing loadJobs/startJobsEvents/trackJob/untrackJob/closeActiveJobSource/history/selection/cleanup surface. Continue using apiFetch and openJsonEventSource.

## 3. Contracts

- Global list URL: /api/generate/jobs; feed: /api/generate/jobs/events; events: jobs/job.
- Every EventSource callback, including open/error/network-error, must verify current source and lifecycle epoch.
- List requests capture epoch, request sequence and accepted-snapshot revision. Old requests cannot apply after cleanup, source replacement, a newer request, or a newer accepted SSE snapshot.
- In-flight polling ownership uses a unique token; an old request's finally must not clear a new lifecycle's token.
- start/load/track activation installs at most one visibility listener. dispose invalidates work before closing source, removes listener and clears timers; explicit reuse can reactivate.
- Poll only when visible and no poll is in flight. Fallback interval remains 5 seconds; current-source open/events stop fallback polling.
- Store callbacks own state, selection pruning and notifications. Keep pure equality/history semantics unchanged during this extraction.

### Per-job tracking (multiple tracked jobs)

- Tracking is keyed by `jobId`. Starting a new job never closes another job's stream; only that job's own terminal state, permanent error, `untrackJob`, `closeActiveJobSource` or `cleanup` releases it.
- Each tracker owns its own source, epoch, fallback timer and in-flight poll token. A released tracker's late SSE/poll/finally callbacks must not revive it or touch a newer lifecycle.
- Re-tracking the same `jobId` de-duplicates: it refreshes the handlers and opens no second source.
- The global feed stays one connection. Accepted `job` events and snapshots are dispatched to every matching tracker (`applyFromFeed`); untracked jobs only feed notifications and the list.
- Fallback polling stays per job at 1200 ms and only while visible and unhealthy. One job's drop must not poll another job.
- `closeActiveJobSource` releases every tracker: clear, preview-group replacement, edit submission and cleanup all use it. Clearing ends display tracking only; the backend job keeps running and stays in the running list/history.

### Preview group aggregation

- A preview group holds ordered submissions. Each click reserves its normalized `n` slots synchronously before the POST; responses attach by a client submission id and events by `jobId`, so reverse-order responses never reorder slots.
- Slot identity is submission + unit + result (+ call for streams), never a global image-array index. `normalizeJobImages` must carry `unit_index` and `result_index` through; falling back to the response array position moves an image into another unit's slot whenever a different unit finishes first. Different jobs' `unit_index=0` do not collide; streaming sequence de-duplication is per submission.
- A failed submission or job changes only its own slots. A terminal job turns every remaining slot into an explicit failed/cancelled state instead of spinning.
- `pendingSlotStatus` checks the unit's own `unit_statuses` entry before the parent's status. A running parent with one failed/cancelled unit must render that unit as failed/cancelled immediately and keep only its siblings pending; testing `isActiveJobStatus(job.status)` first left the failed thumbnail spinning until the whole job finished.
- Late POST/SSE/poll work from a cleared or replaced group cannot mutate the new group; the backend job still exists.
- The first automatic selection is persisted into `selectedSlotId` (`adoptSelection` in `publish`). Without it the "first completed slot" fallback re-points the main image whenever an earlier submission finishes later. An existing selection is never overwritten by that helper, so a user's pick survives a snapshot that momentarily drops its slot.
- The selected image keeps its identity and reports metadata from its own job, not the most recent submission.
- Edit submissions reserve `n` units too: the backend accepts `n>1` for edits, so hardcoding `expectedUnits: 1` drops every extra streamed frame and ends the loading state as soon as the first image lands.

## 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Current snapshot | Apply state, prune selections, observe accepted statuses |
| Old/disposed source callback | Ignore; cannot restart polling or mark healthy |
| Superseded list response | Ignore state and notification effects |
| Current network/feed error | Unhealthy; start visible fallback |
| Hidden page | Skip fallback fetches |
| Visible again | Refresh list once and converge tracked job if needed |
| Repeated cleanup / explicit restart | No duplicate listeners/timers; fresh work accepted |
| Second job tracked while the first runs | Both streams stay open; each releases only itself |
| Released job's late poll/SSE | Ignored; no timer, no state write |
| New submission after a final image | Selected image unchanged; new slot waits in the strip |
| Second unit of an `n=2` job finishes first | Its image takes unit slot 2; slot 1 stays pending, selection does not jump |
| Running job with one failed/cancelled unit | That unit shows failed/cancelled at once; only the remaining units stay pending |
| Later-finishing submission | Main image stays on the first automatic pick |
| Edit submitted with `n>1` | Every unit keeps its own streamed frame and loading state |
| Terminal job with fewer results than slots | Remaining slots become failed/cancelled, never pending |

## 5. Good / Base / Bad Cases

Good: invalidate a tracker's epoch before releasing it so its final callback cannot revive polling.
Base: loadJobs on an active controller applies its current response.
Bad: guarding only onopen while old onEvent/onError can still mutate state.
Bad: one module-level tracked-job singleton, so a new submission silently closes the previous stream.

## 6. Required Tests

frontend/tests/unit/jobsStore.test.ts uses delayed requests and captured EventSource callbacks to verify stale-work rejection, request ordering, disposal/reuse, polling ownership, visibility, selection and notification effects, plus multi-job tracking isolation (independent release, feed dispatch, de-duplication, per-job polling and per-job errors). frontend/tests/unit/previewGroup.test.ts covers ordered submissions, reverse-order responses, error isolation, late-response rejection after clear, terminal slot states, per-unit failure/cancellation while the parent job is still running, stable selection and per-image metadata, out-of-order unit completion, `result_index`-based slot ids and the persisted first automatic selection; previewSlots.test.ts covers streaming frames; editSubmission.test.ts covers multi-image edit unit reservation. Existing preview/history browser flows remain required, including prompt-submit.spec.ts continuous-generation cases and jobs-edit.spec.ts multi-image edit streaming. Run unit/check/build/budget/Worker checks; preserve bundle limits.

Local Playwright runs with host proxies need scoped NO_PROXY/no_proxy=127.0.0.1,localhost and a free isolated PLAYWRIGHT_DEV_PORT. This is a test environment setting, not a repository application configuration change.

## 7. Wrong vs Correct

```typescript
// Wrong: await an old request and apply it without ownership checks.
const jobs = await apiFetch<GenerateJobStatus[]>('/api/generate/jobs');
handlers.onSnapshot(jobs);

// Correct: verify lifecycle, request and snapshot ownership before applying.
if (!active || epoch !== mine || seq !== requestSeq ||
    revision !== snapshotRevision) return;
handlers.onSnapshot(jobs);
```

```typescript
// Wrong: identify a slot by its position in the job's image array.
const byUnit = images.map((image, index) => ({ image, unitIndex: index }));

// Correct: keep the backend's unit/result identity; position is only a fallback.
const byUnit = images.map((image, index) => ({
  image,
  unitIndex: Number.isInteger(image.unit_index) ? Number(image.unit_index) : index
}));
```

```typescript
// Wrong: let the parent's running status mask a unit that already failed.
if (!job || isActiveJobStatus(job.status)) return 'waiting';

// Correct: the unit's own terminal state wins; only an active unit waits.
const unitStatus = job.unit_statuses?.[String(unitIndex)];
if (unitStatus === 'cancelled') return 'cancelled';
if (unitStatus && unitStatus !== 'success' && !isActiveJobStatus(unitStatus)) return 'failed';
if (isActiveJobStatus(job.status)) return 'waiting';
```

```typescript
// Wrong: derive the main image from "first completed slot" on every publish.
const selectedSlot = completedSlots.find((slot) => slot.id === group.selectedSlotId) || completedSlots[0];

// Correct: persist the first automatic pick so a later completion cannot steal it.
group = adoptSelection(group, view);
```

```typescript
// Wrong: a new submission replaces the tracked job and closes the old stream.
closeActiveJobSource();
trackedJobId = jobId;

// Correct: track per job; release only the job that finished.
trackedJobs.track(jobId, { onJob, onError, onPreview });
// on terminal: release(jobId)
```
