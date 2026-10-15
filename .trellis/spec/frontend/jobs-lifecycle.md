# Global generation-job feed lifecycle

## 1. Scope / Trigger

Use when changing global generation-job SSE, fallback polling or jobsStore lifetime. State/equality/history helpers live in stores/jobsState.ts; global feed resources live in stores/jobsFeed.ts; stores/jobs.ts remains the public writable-store facade and retains tracked-preview/history behavior.

## 2. Signatures

```typescript
createJobsFeedController({
  onSnapshot: (jobs: GenerateJobStatus[]) => void,
  onJob: (job: GenerateJobStatus) => void,
  onVisible: () => void
});
// Returns activate(), load(), startEvents(), dispose(), healthy getter.
```

jobsStore preserves its existing loadJobs/startJobsEvents/trackJob/history/selection/preview/cleanup surface. Continue using apiFetch and openJsonEventSource.

## 3. Contracts

- Global list URL: /api/generate/jobs; feed: /api/generate/jobs/events; events: jobs/job.
- Every EventSource callback, including open/error/network-error, must verify current source and lifecycle epoch.
- List requests capture epoch, request sequence and accepted-snapshot revision. Old requests cannot apply after cleanup, source replacement, a newer request, or a newer accepted SSE snapshot.
- In-flight polling ownership uses a unique token; an old request's finally must not clear a new lifecycle's token.
- start/load/track activation installs at most one visibility listener. dispose invalidates work before closing source, removes listener and clears timers; explicit reuse can reactivate.
- Poll only when visible and no poll is in flight. Fallback interval remains 5 seconds; current-source open/events stop fallback polling.
- Store callbacks own state, selection pruning and notifications. Keep pure equality/history semantics unchanged during this extraction.

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

## 5. Good / Base / Bad Cases

Good: invalidate source epoch before close so a final callback cannot revive polling.
Base: loadJobs on an active controller applies its current response.
Bad: guarding only onopen while old onEvent/onError can still mutate state.

## 6. Required Tests

frontend/tests/unit/jobsStore.test.ts uses delayed requests and captured EventSource callbacks to verify stale-work rejection, request ordering, disposal/reuse, polling ownership, visibility, selection and notification effects. Existing preview/history browser flows remain required. Run unit/check/build/budget/Worker checks; preserve bundle limits.

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
