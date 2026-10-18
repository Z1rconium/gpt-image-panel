# Design: simplify existing owners without changing contracts

## Direction and boundaries

Apply the finite candidate set in research/summary.md. No new framework, dependency, persistence model, DTO hierarchy or feature boundary is needed. Maintain the current architecture: API → services → repositories/integrations under the existing layer matrix; runtime remains database-agnostic; frontend stores own remote state and components retain existing lifecycle/UI boundaries.

The same task owns integrated acceptance. Execute serial risk-adjusted batches so there is one writer for a shared source area; use Trellis implementation/check agents with explicit ownership. The main session owns scope decisions, synthesis and final acceptance. Research is complete enough for the selected changes; further inspection is targeted to callers and side effects immediately before edits.

## Backend flow and contracts

1. B1 deletes definitions with no current caller/registration/export. Leave active signed URLs, worker progress adapters and incremental archive construction untouched.
2. B2 removes a superseded per-key method, not RemoteKeyLookup itself. Current LIST/HEAD batching and class fields/defaults remain. Do not import sync into config to revive obsolete references.
3. B3 keeps all established helper signatures and DB facade exports. One helper per pair delegates to the existing implementation. Preserve accepted favorite values and exact LIKE escaping order; do not substitute bool coercion or the distinct tri-state gallery-filter normalizer.
4. B4 removes only verified stale names in the selected gallery/assistant modules, the exact duplicate import and unused local/binding. First trace external attribute uses, fixture patch targets and import-time side effects. `contract_app.proxy`, DB exports, test support exports and the assistant_client test seam stay. No global Ruff autofix. The current global lint debt is evidence for screening, not an instruction to erase 620 names.

No SQL/schema/transaction/executor/auth/middleware algorithm change occurs. Existing HTTP and SSE output contracts remain acceptance oracles.

## Frontend flow and contracts

- F1: gallery search spreads the existing selection-filter payload in its existing JSON position. Preserve keys, trimming, nullable flags and page/cursor/direction/include controls. URL-state serialization remains separate.
- F2: gallery edit parameters start from the existing gallery converter and override current API path, edit-size policy and chroma background policy explicitly. Preserve quantity/compression/format/model defaults, including odd but current accepted values. The job and prompt-only conversion paths retain their distinct contracts. Replace the private response-format pass-through only, not its actual normalizer.
- F3: after existing pointer-capture release, call resetPointerState instead of copying it. Leave pointer-ID guards, click suppression and cancel-specific behavior in place.
- T1: remove only the uncalled ESM script-local predicate. Preserve graph traversal, exit/report behavior and budgets.

No component/template/style redesign or eager-import conversion. New pure characterization tests precede F1/F2 changes. Existing focused browser flows exercise the actual consumers.

## Compatibility trade-offs

Deleting supported aliases to satisfy static diagnostics would reduce lines but break existing usage; retain them. Forcing gallery/image SSE, provider versions or geometry implementations into shared abstractions would add coupling/parameters with little net gain; retain the already documented differences. Whole-tree structural scanning gives candidate discovery coverage, while precise behavior evidence is concentrated on edited paths rather than pretending to prove all lines equivalent.

## Baseline and validation

research/baseline.md records source and environment. Local backend cases are fully accounted for across the sandbox suite and six successful loopback reruns (not one unrestricted run); frontend checks/build/budget/Worker are green. Existing CI provides Python 3.12 validation; local Python is 3.11. The separate undefined logger error path remains a baseline defect, not a hidden refactoring fix.

Run focused characterization/contract/browser checks per batch and integrated checks after the final candidate. Inspect actual workspace/gallery render after browser exercises. Record restrictions with their concrete affected acceptance action; use approved loopback execution where needed instead of modifying product code around the sandbox.

## Recovery and delivery

There are no migrations, dependency updates, release actions or runtime-data changes. Batch diffs are independently reviewable. Undo an unsafe batch by reversing only its task-owned edits after inspecting current state; never reset or clean the whole worktree. Keep code/test changes paired. Preserve pre-existing untracked material. On 2026-10-18 the user approved implementation and phased commits/pushes to the existing GitHub remote without a PR. Commit only task-owned changes on the current branch after batch acceptance; verify remote state before pushing and never force push. Deployment, releases and unrelated files remain outside scope.
