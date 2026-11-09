# Agent asset references

## Scope / Trigger
Agent turn attachment changes and edits must preserve asset snapshots across branches and regeneration.

## Signatures
`AgentTurnRequest.attachments` accepts `{kind: "gallery", image_id}` and `{kind: "asset", collection_id}`. `create_turn` accepts `asset_collection_ids` alongside image IDs.

## Contract
Expand asset collections inside the turn transaction in collection order, excluding SVG/GIF. Deduplicate image IDs, apply the configured attachment limit after expansion, assign normal input labels and save `asset_refs` blocks with alias, collection ID, labels and explicit overlap. Context formats each group as one compact asset line. Regeneration uses the saved snapshot; editing retains referenced snapshots and expands newly selected assets only. Removing a mention removes asset-only images, not explicit attachments or images still used by another asset. Ordinary editing must not depend on loading collections.

## Persistence
Asset mentions are relational rows, not JSON: `agent_message_asset_refs` (one row per mentioned pack: `alias`, `collection_id`, `position`) and `agent_message_asset_ref_images` (members: FK to the `agent_message_images` input row, `position`, `explicit`). `collection_id` has no foreign key so a deleted or changed pack never rewrites an earlier snapshot; members cascade with their input image row, and a deleted gallery image (row kept, `image_id` NULL) is skipped when a snapshot is reused. `create_turn` works on `_AssetGroup` values keyed by gallery `image_id` (`_load_asset_groups`, `_resolve_asset_groups`); never parse `round-N-input-K` labels back into indices. User-message `blocks_json` is written as `[]`. The API still returns `asset_refs` blocks (`id`, `alias`, `collection_id`, `refs`, `explicit_refs`) synthesized by `_attach_asset_ref_blocks` in `list_messages`, `get_message` and `branch_snapshot`; any new message loader must call it. Migration 43 backfills legacy `asset_refs` blocks (idempotent, skips members whose label has no input row) and leaves old `blocks_json` untouched; readers ignore its `asset_refs` entries. Turns created after migration 43 keep their asset refs only in the new tables, so older code cannot read them.

## Validation and errors
Missing or non-asset collections, empty supported collections, missing images and expanded counts above the configured limit fail the turn atomically. Preserve branch revision and source-turn validation.

## Cases
- Existing asset changed after a turn: regeneration and retained edit use its original images.
- Added asset in an edited message: expand its current ordered images once.
- Removed asset with an overlapping explicit image: keep that image.
- Invalid or oversized asset: reject without a partial turn.

## Required tests
Exercise the turn API and repository for ordering, overlap, rollback, branch edits and regeneration after collection changes, deleted member images, conversation cascade, and the legacy-block backfill (including idempotent reruns); test mention parsing, attachment derivation and ordinary edit when collection loading fails.

## Wrong vs correct
Wrong: re-expand every asset when editing an old turn. Correct: preserve retained snapshots and expand only newly attached collections.
