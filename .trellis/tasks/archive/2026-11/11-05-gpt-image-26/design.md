# GPT Image 2.6 design

## Architecture and wire contract
Extend existing capabilities, form state, FastAPI schemas and job pipeline; do not create another generation pipeline. Add exact gpt-image-2.6-flare and gpt-image-2.6-sunburst IDs (no invented snapshots). Use JSON Images generations and multipart Images edits. Both 2.6 models support auto/low/medium/high/xhigh/max quality. Nullable complexity is low|medium|high, omitted for older models; UI default is null (model default high). Add output_format svg|gif to generation; edits remain png|jpeg|webp. Add nullable frame_count and frame_duration_ms; normalize GIF defaults to 9 and 200; duration values must be multiples of 10. Reject non-null animation fields outside GIF. For SVG/GIF omit stream=false as well as all partial_images/compression/prompt_rewriter wire fields. Reject stream=true rather than hiding it server-side. SVG n must be 1 at panel admission, not just each fanned-out upstream unit. Reject chroma backgrounds for SVG/GIF. Keep old raster normalization untouched.

The UI may explicitly reset incompatible choices with a visible localized notice; direct API invalid combinations return 422. GIF explicit size accepts positive multiples of 16 and aspect <=3:1 without the static minimum-pixel bound. Keep documented edge constraints; upstream enforces unpublished aggregate animation budgets, with actionable errors and no automatic scaling/frame reduction.

Add complexity, frame_count, frame_duration_ms, markup_applied to job/gallery response types and persistence; nullable columns for old rows. Include new values in unit request snapshots and archive metadata. Markup file input is multipart field markup in both existing edit routes. EditImageSource role gains markup. Primary orientation must normalize to upright for markup (do not apply the legacy raw-mask orientation exemption). Markup is PNG with at least one transparent pixel, same oriented dimensions as primary, both <=16777216 pixels. Cap file at min(existing deployment file cap, 50 MiB). Reject mask+markup and suppress paste-back. Support uploads and gallery-primary edits. Store recoverable per-job markup alongside existing mask assets; identity-checked GET/POST restoration analogous to mask (use existing identity mechanisms), delete with job, account for pending-source bytes, clean after queue rejection/cancel/failure/recovery.

Provider v2 gains files.markup, {{markup}}, {{complexity}}, {{frame_count}}, {{frame_duration_ms}} and explicit derived support. Support multipart and JSON data URL markup. Extend format allowlist but retain default allowed formats png/jpeg/webp for existing unspecified configs. Explicit unsupported controls fail before admission. Preserve v1 behavior.

## Media integration boundary
Media owner provides backend/app/core/generated_media.py with these public helpers for backend integration:
- detect_generated_image_format(data: bytes) -> str | None: raster sniff plus SVG.
- validate_generated_image_header_bytes(data: bytes, *, filename: str = '', content_type: str = '') -> str: existing raster header checks, complete safe SVG structural validation.
- get_generated_image_dimensions(data: bytes) -> tuple[int, int] | None: SVG dimensions from width/height or viewBox; raster dimensions with existing metadata/Pillow fallback.
Backend owner uses these for upstream output detection/header validation/diagnostics; all input image validators stay raster-only. Media owner handles downstream file saving, full file validation, MIME, safe paths and archives.

Validate SVG using defusedxml and static-content policy: forbid DTD/entities, script, on* handlers, foreignObject, image, animation, external hrefs, CSS @import and nonfragment URL references. Allow internal fragment refs. Preserve accepted XML bytes. Serve SVG as image/svg+xml with restrictive CSP (sandbox; default-src none; style-src unsafe-inline) and nosniff; use img, never inline HTML. CairoSVG >=2.9.1 + runtime Cairo creates only thumbnail-sized raster under existing image work limits, with external fetch denied, unsafe=False and isolated 10s subprocess timeout. Save SVG separately from raster/Pillow admission. Width/height must resolve positive with bounded raster dimensions; use viewBox for relative/absent root size. No auto rasterization for edit inputs.

GIF validator fully loads each frame and enforces existing image resource limits; original bytes must not be reencoded. Static thumbnail uses frame zero, gallery/lightbox original img animates. Disable incompatible edit/reference/raster-only actions based on actual media format without affecting previous custom-model inputs.

2.6 built-in rates: text input 5, image input 8, image output 30 USD/M. Do not change other models' rates. SVG cost is incomplete when author-model usage/rate is unavailable; do not infer image_output_tokens from undifferentiated output_tokens for SVG.

## Ownership and integration
Three parallel gpt-6.1-sol trellis-implement agents, same checkout:
1. backend: all backend API/core models/schemas/provider mappings/job lifecycle/DB migrations, image costs, upstream adapters and all of integrations/upstream/generation.py, plus their tests. Do not edit media-owner files below.
2. media: core/media.py, new core/generated_media.py and SVG helpers, repositories/image_files.py, thumbnails.py, thumbnail_jobs.py, archive/sync media-specific code, gallery media routes/headers, requirements.txt/lock and Dockerfile, own media tests. Coordinate with backend for any other shared module.
3. frontend: frontend/** including UI/API types/tests/locales. Use the published backend contract above and coordinate deviations before changing it.
Main: task/spec records, README documentation, changelog, integration and final acceptance. Workers are not alone; never revert others' edits, do not commit/push, do not spawn further agents. Each owner makes adjacent in-scope repairs in its write area. Communicate cross-owner needs rather than editing each other's files.

## Compatibility and rollout
Add nullable/defaulted fields only, idempotent additive SQLite migrations. Keep existing entry points and defaults. Dependencies added through hashed requirements.lock; Linux runtime includes Cairo. Local install docs explain Cairo prerequisite. No release version bump or deployment. Back up live data before any future deployment/migration; tests use isolated fixture DBs only. Rollback is code rollback with new files/nullable columns retained, no destructive cleanup.
