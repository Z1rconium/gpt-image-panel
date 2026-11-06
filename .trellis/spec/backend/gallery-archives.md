# Gallery archive collections

## Scope / trigger
Changes to gallery ZIP export/import, collection metadata, image identity or ordering must preserve this round-trip contract.

## Signatures
Full and selected gallery exports produce `metadata.json` and `metadata.ndjson`. Collection records are `{id, name, asset_alias, is_default, image_ids}`; NDJSON uses `{type: "collection", collection: {...}}`. `read_archive_collections(path)` validates records; `import_gallery_entries(..., image_id_map=...)` maps source IDs to committed IDs; `restore_gallery_collections(collections, image_ids)` creates new collections.

## Contract
Metadata includes each exported collection's members in `position IS NULL, position, added_at, image_id` order, restricted to images actually included in the ZIP. Full exports include empty collections; a collection export includes its selected collection even when its asset pack is empty. Only the exported subset of members is restored. Legacy archives with no collection records retain their existing import behavior. Restoration appends collections without replacing existing ones; collisions in names and aliases are resolved case-insensitively by `(N)` and `_N` suffixes, respectively, truncating the base to the field limit. Image IDs are remapped after import, including ID collisions. Missing images are omitted. The archived default is restored only when the target has no default; an existing target default is never changed. A metadata-only archive can restore empty collections; a normal empty gallery or empty ordinary-collection export retains its previous rejection behavior.

## Validation and errors
Malformed collection records, invalid names or aliases, duplicate member IDs, ambiguous source image IDs and excessive member counts reject the import. The import limit is 10,000 image entries and 10,002 ZIP files (including both metadata files); archive, per-file, metadata and total uncompressed byte limits and compression-ratio checks remain in force. The metadata byte limit bounds collection records. Collection metadata is validated before image writes. Existing batch import semantics apply: image batches are committed independently before the collection restoration transaction. A failure after a batch commits can therefore leave imported images and must not be described as an atomic archive import.

## Cases
- Full export with empty asset packs and reordered members: import reproduces each order and empty pack.
- Selected export: each included collection contains only selected, available members.
- Conflicting 32-character alias: truncate before appending `_1` and preserve the original existing collection.
- Old ZIP with only image metadata: import images as before.
- Broken or duplicate source identifiers: reject rather than attach images to ambiguous collections.

## Tests required
Exercise actual API export/download/upload/job completion for both metadata encodings, ID/name/alias collisions, ordering, missing files and empty packs; test older archives and malformed metadata before writes. Verify direct and job-based exports and existing empty-gallery/ordinary collection behavior.

## Wrong vs correct
Wrong: assume imported images retain their source IDs. Correct: map IDs from the committed import batch when creating collection members.
