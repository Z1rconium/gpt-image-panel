# Changelog

## v1.7.2

- Agent turn admission now reserves capacity before database waits, including pending creates in the per-worker `AGENT_MAX_ACTIVE_TURNS` limit. Idempotent replays remain available at capacity, and request cancellation completes admission so queued turns receive a runner.
- Assistant slot acquisition uses one deadline for local semaphore waits, SQLite attempts and retries. Late leases are released after timeout or cancellation; the deadline does not shorten an admitted model request.
- JSON body limits follow registered route declarations even with absent or incorrect Content-Type, including chunked bodies rejected before parsing with 413. Agent `after` cursors must fit a nonnegative SQLite 64-bit integer (422 otherwise); invalid or non-ASCII Last-Event-ID values are ignored and synthetic terminal cursors cannot overflow.
- Stale-turn cleanup rechecks expiration inside its write transaction. Lost or expired execution leases stop the Agent and fence business writes; interruption, cancellation, shutdown and failures clean up pending jobs and edit-source staging files while preserving established terminal states and gallery images. Text snapshots track modification versions and retry failed writes without losing changes made during persistence.
- Vision previews use the bounded image/file executors and a separate strict `VISION_PREVIEW_MEMORY_BUDGET_MB` budget (256 MiB per process by default, range 32–16384, configurable in Overall Config). Estimates use decoded pixels × 16 plus three preview byte limits, with JPEG draft dimensions. Oversized Agent previews are skipped without removing text references or original images; direct Assistant analysis returns 400 if a preview cannot fit.
- Preview loading uses an ordered window of at most three items and stops scheduling at byte/image limits; tool results load at most four successful previews. Concurrent cache misses share a decode unaffected by caller cancellation. Cache keys include file state and all preview safety settings; clear operations fence older fills, and the 32-entry / 15-minute / 24-MiB LRU accounts for retained data-URL string memory, expires old entries and does not cache oversized entries.
- Added resource/cache counters and gauges, boundary regressions, and reproducible cold/hot/shared/distinct preview benchmarks. See `VISION_PREVIEW_PERFORMANCE.md` for commands, measurements and interpretation. No SQLite migration is required.

## v1.7.1

- Security hardening for Agent mode: message text and attachments are validated against the configured limits in the request schema, oversized Last-Event-ID cursors are ignored instead of raising, edit-source temp files only reuse known image suffixes, and the startup sweep no longer deletes staging files a running turn may still use. All `AGENT_*` overall-config overrides now have upper bounds.
- Agent runner reliability: tool calls per model round are capped, a saturated AI Assistant concurrency pool fails the turn after a bounded wait instead of holding it open, background tasks are awaited on turn teardown, and the SSE tail closes cleanly on backend errors so the browser can reconnect.
- Agent performance: turn events are written in batches with caller-owned sequence numbers, block snapshots persist on the existing 1s cadence, idle stale-turn sweeps no longer take the write lock, and migration 32 adds image-order, pending-image and finished-turn indexes. Vision previews are cached in-process (bounded LRU) and loaded concurrently, the model request body is serialized off the event loop, and conversation detail only returns image references for the messages in the page.
- Agent web UI: returning to a tab resumes the event stream from the last applied event instead of replaying the turn, cancel leaves the "stopping" state through a status-poll fallback, stream frames paint once per animation frame, recovery attempts reset per conversation, and an abandoned reply is marked interrupted.

## v1.7.0

- Agent conversation mode: a Studio/Agent switch in the header opens a multi-turn chat in which the model plans and creates images. Enable it under Settings → AI Assistant; it reuses the Prompt Optimizer endpoint and needs a model that supports function calling (chat/completions or responses).
- The Agent calls `generate_image_batch` (independent images run concurrently) and `continue_generation` (a later round for images that depend on earlier ones). Every image goes through the normal job queue, so presets, keys, concurrency limits, cost tracking, webhooks, and the gallery all apply. Referencing an earlier image with `@round-N-image-M` (or `@第N轮图M`) edits it instead of generating from scratch.
- Conversations, turns, messages, and image references are stored in SQLite (migration 31). Turns run in the background and record replayable events, so a reload, a second tab, or another worker can follow a reply (`GET /api/agent/turns/{id}/events`, resumable with `?after=` or `Last-Event-ID`). Stop cancels the turn and its queued image jobs; a turn whose worker died is marked interrupted.
- Deleting a conversation keeps its gallery images. Deleting a gallery image marks it as deleted in the conversation and hides it from `@` suggestions.
- New `AGENT_*` limits (also in Overall Config and `.env.example`) cap conversations, turns, message and prompt length, attachments, images per batch and per turn, history images, tool rounds, and concurrent turns.
- The main stylesheet budget in `bundle-budget.mjs` is raised from 12.9 to 13.3 KiB for the new view's utility classes.

## Unreleased

- Mask edge smoothing now only adds editable pixels; it never removes a painted area.
- Automatic gap filling is enabled by default in the mask editor. The added area is visible in the preview and coverage readout.
- Imported soft alpha masks treat pixels with alpha below 128 as editable. Black and white masks use white as the editable area; same-ratio masks can be scaled on import.
- Masked edits paste the model result back onto the primary image by default. The final result reports whether paste-back was applied or why it was skipped; the switch can be turned off for each edit.
- The preview of a masked edit can switch between the final result and the primary image used for that request.
- The paste-back drift guard now compares red, green, and blue separately, so a color shift that leaves luminance unchanged is also caught.
