# First-batch architecture design

## Boundaries

Retain API → services → repositories/integrations → runtime/core and Svelte writable stores. No new framework/dependency.

- Database child: runtime owns pools, generic scheduling, metrics and memory admission; repositories/db/execution owns SQLite scopes, retries and DB-aware shutdown.
- Coordination child: five persistence domains behind repositories.coordination's stable operation facade.
- Frontend child: pure job state helpers and a global-feed controller; jobsStore retains history/selection/tracked-preview integration.

Detailed boundaries are in child design.md files; source evidence is research/audit.md.

## Ordering and Compatibility

Start database runtime first because it migrates caller imports across API/services/gallery mutations. Finish its migration before coordination's backend check. Frontend implementation can run concurrently because it owns disjoint files; its final verification remains independent. Parent owns final integration review.

No schema migration, protocol change or UI redesign. Internal DB helper import locations change together with callers. Preserve coordination operation signatures and tracked-job/history behavior.

## Risks

- Mixed runtime imports: move DB helpers only; leave image/file operations in runtime.
- Lease extraction: preserve SQL and transaction boundaries; test owner fencing, cancellation and atomic admission.
- Frontend resource lifetime: delayed-promise/fake-source tests must fail on baseline behavior.
- Narrow bundle margins: production checks use unchanged limits.
- Separate Bootstrap Guidelines work: add scoped learned contracts without taking ownership of prior work.

## Rollback

Restore a child implementation and all its imports/tests together if needed. No persisted-data rollback is required. Do not use broad reset/checkout operations that discard unrelated work.
