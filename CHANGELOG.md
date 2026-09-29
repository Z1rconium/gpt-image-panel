# Changelog

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
