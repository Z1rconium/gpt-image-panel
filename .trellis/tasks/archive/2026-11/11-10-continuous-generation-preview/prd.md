# Continuous generation and grouped preview

## Goal
Allow repeated Generate submissions while earlier requests are pending, retaining their results in one preview with pending thumbnail indicators.

## Background
PromptForm.svelte:152 and :401 gate submission on loading. preview.ts:243 replaces preview on submission. jobs.ts:196 tracks only one job. PreviewPanel.svelte:239 prioritizes the streaming grid; :349 renders completed thumbnails only.

## Requirements
- R1: Each valid click creates an independent request with a snapshot of the current form and its normalized quantity, including while previous POSTs are pending.
- R2: Aggregate submissions in click order in the current preview group, including submissions after earlier images finish. Clear or explicit task navigation establishes a new group.
- R3: Before final output, retain waiting/streaming feedback. Once a final image exists, retain the selected main image and show pending slots in the thumbnail strip, replacing them in place on completion.
- R4: Isolate errors, cancellation, partial frames and final results by submission/job/unit. Late work from an old group cannot mutate a new group.
- R5: Preserve form validation, clear-prompt preference, per-request quantity limits, history, notifications, retries, edit preview and selected-image actions.

## Acceptance
- R1/R2: Three clicks during a delayed POST create three requests; reverse-order responses retain all submissions.
- R2/R3: Two n=2 requests show four initial slots in submission/unit order; results remain accessible irrespective of completion order.
- R3: An existing final image remains selected when another request starts or completes; its new pending thumbnail animates immediately.
- R4: Failure/cancellation in one job does not remove other images or stop their progress; repeated frames do not cross jobs or overwrite final images.
- R4: Clear/navigation followed by late POST/SSE/poll responses cannot restore the previous group.
- R5: History navigation, edit comparison/masks, regenerate, downloads, metadata and clear-prompt preference retain their established meanings; narrow-screen and keyboard flows work.

## Out of scope
Backend queue/API changes, persisted preview groups, new continuous-edit behavior, group cancellation and automatic submission retry.

## Planning decisions
Retain the current group after all jobs finish, until clear or explicit task navigation. Reuse multi-image presentation with a horizontally scrollable thumbnail strip. Actual execution concurrency remains controlled by the existing backend. No blocking product question remains for this plan.
