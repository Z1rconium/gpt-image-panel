# Job state and global-feed ownership

- stores/jobsState.ts: existing pure equality/list/set/history helpers with unchanged semantics.
- stores/jobsFeed.ts: global SSE health, fallback polling, request sequence, lifecycle epoch, snapshot revision and visibility listener.
- stores/jobs.ts: public facade, writable state, history/selection, notifications and existing tracked-preview controller.

Keep tracked-job generation/history sequence guards. Extracting tracked-preview code again is unnecessary for this batch.

## Lifecycle

Explicit start/load activates a usable lifecycle. Cleanup invalidates epoch, stops timers, removes listener and closes source. Reuse installs one listener. Every source callback verifies current source/epoch, including onopen and errors.

List loads capture epoch/request sequence/snapshot revision; apply only if current and no newer snapshot superseded them. Obsolete finally blocks cannot reset newer in-flight markers. Typed callbacks separate fetch/apply/single-job/visible-page convergence from store state. Continue using apiFetch/openJsonEventSource.

## Compatibility and Checks

Preserve jobsStore methods/JobsState shape, URLs/events, intervals, reconnect/fallback behavior, notification observation, history semantics and existing equality optimization. Test delayed responses, captured source callbacks and resources through public behavior. Restore helper/controller/facade changes together if needed.
