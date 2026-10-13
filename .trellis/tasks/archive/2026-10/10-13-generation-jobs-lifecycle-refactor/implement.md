# Jobs lifecycle implementation

- [x] Regressions for late responses, old source events/errors, request races and restart visibility.
- [x] Extract pure jobsState helpers unchanged.
- [x] Extract jobsFeed global lifetime and current-source/epoch/request guards.
- [x] Delegate store methods; preserve tracked/history integrations.
- [x] Run npm --prefix frontend run test:unit and npm --prefix frontend run check.
- [x] Run production build, check:bundle and check:worker-build.
- [x] Run npm --prefix frontend run test:e2e -- jobs-edit prompt-submit phase-five.
- [x] Trellis check timers/listeners/notifications/hidden-visible behavior and store consumers.
- [x] Capture global-feed contract and validation.

Ownership: jobs.ts, jobsState.ts, jobsFeed.ts and focused unit/E2E tests. Keep workspace/UI unchanged; report a proven integration need before expanding scope.

Results and review evidence: validation.md. Contract captured in .trellis/spec/frontend/jobs-lifecycle.md.
