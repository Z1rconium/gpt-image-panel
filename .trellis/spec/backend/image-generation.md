# Image generation capabilities and original media

## Scope

This contract covers admission through upstream requests, recoverable image units,
job/gallery metadata and original-file delivery. Extend the existing pipeline;
keep `gpt-image-2` as the default and preserve custom models and saved presets.
Verified model additions are `gpt-image-2.6-flare` and
`gpt-image-2.6-sunburst` along with official dated snapshots `gpt-image-2.6-flare-2026-11-04`
and `gpt-image-2.6-sunburst-2026-11-04`; do not invent pluralized model IDs.

## Entry points and signatures

- `POST /api/generate`: `GenerateRequest` JSON.
- `POST /api/edits` and `/api/edits/from-gallery/{image_id}`: `EditRequest`
  multipart scalars, image/reference files and optional `markup` PNG file.
- `GET /api/generate/{job_id}/markup`: original persisted markup.
- `POST /api/generate/{job_id}/markup/restore`: form with exactly one
  `source_sha256` or `gallery_image_id`, verified against the job's primary source.
- `core/generated_media.py` owns output validation:
  `detect_generated_image_format(bytes) -> str | None`,
  `validate_generated_image_header_bytes(bytes, *, filename='', content_type='') -> str`,
  `validate_generated_image_bytes(bytes, *, filename='', content_type='') -> str`,
  `get_generated_image_dimensions(bytes) -> tuple[int, int] | None`.
- `core/media.py` retains raster-only edit-input validators. Generated SVG must
  never expand the edit-input allowlist.

## Data and validation contracts

Resolve the preset's effective model before model-specific admission. The 2.6
models use Images generations/edits only, six qualities (`auto`, `low`, `medium`,
`high`, `xhigh`, `max`) and optional `complexity` (`low`, `medium`, `high`). Omit
complexity for the upstream default high; omit `response_format` and
`input_fidelity` for 2.6 requests.

`svg` and `gif` are generation-only. SVG requires parent `n=1` before fanout.
Neither format sends streaming, partial-image, compression or prompt-rewriter
fields; reject incompatible requests rather than silently discarding explicit
controls. Chroma removal remains raster-only. GIF uses `frame_count` 2..64
(default 9) and `frame_duration_ms` 20..2000 in increments of 10 (default 200).
Frame fields are GIF-only. GIF explicit dimensions keep positive multiples of
16, side <=3840 and aspect <=3:1, but do not inherit the static minimum-pixel
bound. Do not invent an undocumented aggregate frame-pixel budget.

Markup is a separate transparent PNG, aligned to the upright first input image,
with at most 16777216 pixels per image. It cannot coexist with a mask and never
runs paste-back. Its byte limit is the lower of the deployment file limit and
strictly less than 50 MiB. Primary/reference raster inputs for 2.6 are PNG,
JPEG or WebP, each strictly below 50 MiB. The legacy raw-mask EXIF exception
applies only to masks, not markup. Account for markup bytes in both HTTP body
limits and pending-source reservations.

Persist nullable `complexity`, `frame_count`, `frame_duration_ms`,
`markup_applied` and `safety_identifier` in job and gallery rows, unit request snapshots, API/SSE and
archive metadata. A markup source has role `markup`. Its recoverable original
is `{job_id}-markup.png` under the existing mask asset directory. Preserve
owned files during startup GC and remove them with deleted jobs. Temporary
sources and reservations must be released on rejection, failure and cancellation.

Provider v2 explicitly maps `{{complexity}}`, `{{frame_count}}`,
`{{frame_duration_ms}}`, and markup via `files.markup` (multipart) or `{{markup}}`
(JSON data URL). Admission checks the actual operation's mapping, not merely
whether another operation sends that field. Unspecified format capabilities
remain PNG/JPEG/WebP; SVG/GIF require an explicit declaration.

SVG is static XML: reject DTD/entities, active elements, handlers, embedded or
external images/resources and unsafe CSS; permit local fragment references.
Validate finite positive bounded dimensions, including rounded raster bounds.
Preserve accepted XML bytes, serve with `image/svg+xml`, restrictive sandbox CSP
and `nosniff`, and display with `img`, never inline HTML. CairoSVG >=2.9.1 renders
only bounded thumbnails in a timed isolated process with external fetching
disabled. GIF validation loads every frame; originals retain every byte and
frame while thumbnails use frame zero.

2.6 built-in rates are USD/M tokens 5 text input, 8 image input, 30 image output.
Preserve environment overrides and raw usage. SVG output tokens can include
unpriced authoring: never infer all output tokens are image tokens, and report
an incomplete estimate when authoring cost is unavailable.

## Error matrix

| Condition | Outcome |
|---|---|
| Invalid model/format/control combination | 422 before queue admission |
| Invalid/malformed uploaded image | 400 or 422, no persisted job or leaked source |
| Deployment/body/file byte cap exceeded | Existing request-too-large response |
| Missing saved markup/job | 404 |
| Missing or ambiguous restore identity | 400 |
| Restore source differs or is unavailable | 409, do not attach markup |
| Unsafe SVG or malformed later GIF frame | Output fails validation, no gallery original |
| Thumbnail rendering failure/timeout | Existing thumbnail failure handling; original remains |
| Missing SVG authoring usage/rate | Cost incomplete with an explanatory reason |

## Good, base and bad cases

- Base: old PNG generation/edit and custom provider presets keep their original
  entry points and defaults.
- Good: Sunburst GIF with 2 frames, 100 ms, `32x32`; Flare SVG with n=1;
  upright primary plus equal-sized transparent markup; retry with matching bytes.
- Bad: SVG n=2, SVG edit, GIF streaming, non-GIF frame fields, mask plus markup,
  markup after changing primary, SVG/GIF as edit/reference inputs, or a provider
  request whose explicit control is absent from its actual wire template.

## Required tests

Use real SVG, two-frame GIF and PNG binaries. Public API tests must trace model
and parameters to actual upstream JSON/multipart, then persisted metadata,
original download/MIME, dimensions and restore identity. Cover unsafe XML/CSS,
truncated later frames, archive roundtrip, bounded thumbnails, startup GC,
queue rejection/cancellation and source reservation cleanup. Preserve the old
2.5 and provider contract suites. Browser tests must verify loaded originals,
not just image URLs or base64-looking strings.

## Wrong and correct

```python
# Wrong: a generated-file extension becomes an admitted edit input.
EDIT_EXTENSIONS |= GENERATED_EXTENSIONS
# Correct: separate admission boundaries.
validate_image_bytes(edit_input)
validate_generated_image_bytes(generated_original)
```

```python
# Wrong: fanout turns an invalid parent SVG request into valid unit requests.
units = [request.model_copy(update={'n': 1}) for _ in range(request.n)]
# Correct: validate the parent n/format combination before creating units.
request = GenerateRequest.model_validate(body)
```
