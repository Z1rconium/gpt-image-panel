# Complete GPT Image 2.6 support

## Goal
Let panel users generate and edit with GPT Image 2.6 Flare and Sunburst, including uploaded markup, SVG and animated GIF, without breaking existing models, presets or job workflows.

## Approved decisions
The user approved the full plan, Trellis task creation and execution. They selected uploaded transparent PNG markup only (no drawing UI), parallel gpt-6.1-sol sub-agents, and main-agent final acceptance. Keep gpt-image-2 as the default; preserve saved/custom models. Do not publish, push or deploy. No paid upstream calls without credentials and cost authorization.

## Requirements and acceptance
- R1: Both exact model IDs are selectable in generation and preset settings; six quality levels and optional low/medium/high complexity work end to end. Omitted complexity uses upstream high. Images API only for 2.6; omit response_format and input_fidelity.
- R2: SVG generation requires n=1. GIF generation accepts 2..64 frames (default 9), duration 20..2000 ms (default 200, increments 10). SVG/GIF are generation-only, nonstreaming, without compression, partial_images or prompt_rewriter; frame fields are GIF-only. Do not invent an undocumented GIF total-pixel budget or apply the static-image minimum pixels to GIF.
- R3: Upload one transparent PNG markup layer separately from primary raster image; preview overlay, replace/remove, bind to primary and discard on primary change. No drawing UI. Validate oriented dimensions equal, each <=16777216 pixels, markup <50 MiB and existing deployment byte caps. Reject mask+markup. No paste-back for markup.
- R4: New parameters and markup survive admission, persistence, unit recovery, SSE/status, history reuse/retry and gallery metadata. Markup source reservation/cleanup and identity-checked restoration follow existing mask contracts.
- R5: SVG remains an original vector file, is safely validated/served and previewed with img, gets a bounded raster thumbnail. GIF remains the original animated bytes, uses first-frame thumbnail and animates in preview/lightbox. Correct MIME, download, archive roundtrip, sync and deletion work. No automatic conversion to editing inputs.
- R6: Custom provider v2 mappings can explicitly map new params and markup; no silent dropping of requested controls. Existing provider mappings retain their original three-format defaults.
- R7: Built-in 2.6 pricing uses current official text-input/image-input/image-output rates (5/8/30 USD per million). Preserve env overrides and raw usage. SVG authoring charges must not be presented as a complete image-only estimate.
- R8: Existing ordinary generation/edit, masks, paste-back, custom models, preset import/export and usage reporting retain behavior. New constraints apply only to new capabilities. Both existing UI locale catalogs (en and zh-CN) and all three README translations describe new features.

## Acceptance evidence
Backend contract/unit tests exercise actual wire fields and negative cases, recovery/cleanup, SVG content safety, GIF preservation and archive roundtrip. Frontend type check, unit tests, build and Playwright exercise model/format transitions, markup overlay and retry, original downloads, SVG preview and GIF animation. Main agent inspects actual desktop/mobile renders and integrates the complete request.

## Out of scope
New annotation canvas; default-model migration; SVG/GIF editing; paid live calls; deployment or remote publication. No blocking product questions remain.
