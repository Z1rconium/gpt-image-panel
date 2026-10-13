# Refactor generation jobs lifecycle

## Goal and Evidence

Prevent stale global job updates across cleanup/replacement and make singleton resources reusable. Parent research/audit.md F1 anchors jobs.ts:164/:306/:555 and records two read-only reproductions. Listener installation at :550 is not restartable.

## Requirements

- GJ1: Separate pure state from global SSE/polling lifetime.
- GJ2: Ignore old callbacks/list responses after replacement/disposal.
- GJ3: Preserve updates, selection pruning, notifications, history and tracked preview.
- GJ4: Restart visibility/fallback resources without duplicate listeners/timers.
- GJ5: Older HTTP snapshots must not replace newer accepted snapshots.

## Acceptance Criteria

Delayed promises/fake sources demonstrate obsolete events/responses/errors are ignored and cannot restart timers. Fresh loads/current events still work after reuse. Concurrent loads converge to newest accepted data. Hidden tabs do not poll; cleanup is idempotent. Tracked/history/notifications retain contracts. Frontend diagnostics/unit/build/budget/Worker/focused E2E pass at unchanged budgets.

## Out of Scope

Visual redesign, wire/history changes, Agent/gallery refactors and equality-semantics changes without demonstrated defects.
