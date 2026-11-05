# Main-agent acceptance — GPT Image 2.6

## Result

Implementation accepted locally on 2026-11-05 on branch `codex/gpt-image-26`.
The default remains `gpt-image-2`. Exact supported additions are
`gpt-image-2.6-flare` and `gpt-image-2.6-sunburst`.

Parallel owners delivered backend/provider, media and frontend work using
`gpt-6.1-sol`, as selected by the user. Main integrated and accepted the result.
The independent read-only cross-layer check found an SVG normalized-usage
regression; the backend owner corrected it and main public API tests confirmed
both explicit-token preservation and the visible authoring-cost gap.

## Acceptance evidence

| Area | Evidence |
|---|---|
| Full backend | 1254 passed, 23 skipped, 2 dependency deprecation warnings; `/private/tmp/gpt-image-26-backend-final.log` |
| Main public API acceptance | 8 cases in `backend/tests/test_image_26_acceptance_contract.py`: both models and actual SVG/GIF original bytes through wire, queue, gallery and download; markup upload/gallery, metadata, source-identity restore, history deletion; SVG explicit/undifferentiated usage and incomplete authoring cost |
| Assistant context delta | 30 assistant contract tests passed after accepting SVG/GIF current-format context; recommendations retain existing raster output formats |
| Frontend type/unit | Main Svelte check: 0 errors/0 warnings; full 360 unit tests passed. Final edit-source changes also passed the 12-case focused suite |
| Frontend production | Final check/build passed after EXIF/CDN/media gating fixes. Main bundle gate: homepage JS gzip108034/122880 B; CSS14796/14848 B; OGL14955/32276 B |
| New and old model browser contracts | Existing image-25 plus initial image-26 group15 passed; added EXIF known/unknown metadata and same-origin original under CDN preview passed; final SVG/GIF load/download/gating cases passed |
| Existing browser regressions | Main mask/edit/gallery/responsive group114 passed,3 skipped. The2 failures were a broken local Python path and a pre-existing stale security-copy assertion; test-only portability/copy fixes passed both cases afterward |
| Actual visual inspection | Main inspected Flare desktop/mobile controls, visible markup overlay, and stable SVG/GIF lightboxes; loaded originals use img with correct dimensions. Both models have desktop/mobile screenshots |
| Dependency integration | Hashed universal lock preserves existing pins; CairoSVG2.9.1+defusedxml added, installed into isolated test environment; real Cairo thumbnail and archive roundtrip tests passed |

The23 backend skips are22 opt-in performance cases and1 Docker Compose case
(Docker unavailable). No Docker image build, live user-data migration or paid
upstream call was performed. Those operational checks belong to future deployment.
The local repository .venv Python symlink was already broken; tests used an
isolated `/private/tmp/gpt-image-26-venv` without changing it. Loopback protocol
and browser tests ran with explicit local network permission and test-process
proxy overrides, not persistent environment changes.

## Corrections made during acceptance

- Kept old edit callable signatures usable when markup is absent.
- Preserved markup originals through startup GC and removed them with job history.
- Persisted markup flags and new scalar values in gallery/job/unit/archive paths.
- Bounded markup body admission by the deployment cap; released source reservations.
- Validated the actual provider operation's templates instead of union capabilities.
- Preserved explicit SVG image-token breakdown and authoring-cost reasons through aggregation.
- Retained raster-only input boundaries while admitting safe SVG originals in archives.
- Validated upright EXIF dimensions for gallery markup from the same-origin original,
  independently of CDN preview URLs or raw stored dimensions.
- Disabled incompatible SVG/GIF editing/reference actions and SVG raster-vision actions.

## Sources and usage

Official model/wire/pricing evidence is in `research/official-api.md`. User docs
are in README.md, README.zh-CN.md and README.zh-TW.md. The executable cross-layer
contract is `.trellis/spec/backend/image-generation.md` with frontend companion
updates in `.trellis/spec/frontend/forms-and-api.md`.

Screenshots remain in `/tmp/gpt-image-2.6-{flare,sunburst}-{desktop,mobile}.png`,
`/tmp/gpt-image-26-markup-overlay.png`, and
`/tmp/gpt-image-26-{svg,gif}-{preview,lightbox}.png`.

## Delivery state

Local implementation accepted. User subsequently authorized split commits, push, merge into main and GitHub Actions Docker v1.9.0 publication. Release CI remains the remote publication gate. Pre-existing untracked infrastructure is excluded.
