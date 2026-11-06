# Agent asset references

## Scope / Trigger
Agent turn attachment changes and edits must preserve asset snapshots across branches and regeneration.

## Signatures
`AgentTurnRequest.attachments` accepts `{kind: "gallery", image_id}` and `{kind: "asset", collection_id}`. `create_turn` accepts `asset_collection_ids` alongside image IDs.

## Contract
Expand asset collections inside the turn transaction in collection order, excluding SVG/GIF. Deduplicate image IDs, apply the configured attachment limit after expansion, assign normal input labels and save `asset_refs` blocks with alias, collection ID, labels and explicit overlap. Context formats each group as one compact asset line. Regeneration uses the saved snapshot; editing retains referenced snapshots and expands newly selected assets only. Removing a mention removes asset-only images, not explicit attachments or images still used by another asset. Ordinary editing must not depend on loading collections.

## Validation and errors
Missing or non-asset collections, empty supported collections, missing images and expanded counts above the configured limit fail the turn atomically. Preserve branch revision and source-turn validation.

## Cases
- Existing asset changed after a turn: regeneration and retained edit use its original images.
- Added asset in an edited message: expand its current ordered images once.
- Removed asset with an overlapping explicit image: keep that image.
- Invalid or oversized asset: reject without a partial turn.

## Required tests
Exercise the turn API and repository for ordering, overlap, rollback, branch edits and regeneration after collection changes; test mention parsing, attachment derivation and ordinary edit when collection loading fails.

## Wrong vs correct
Wrong: re-expand every asset when editing an old turn. Correct: preserve retained snapshots and expand only newly attached collections.
