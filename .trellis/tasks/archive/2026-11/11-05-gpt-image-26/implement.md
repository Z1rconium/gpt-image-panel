# Implementation and acceptance

The user approved the plan in chat and then explicitly said to start execution. Model choice is gpt-6.1-sol; main does final acceptance.

1. Curate context and activate this task. Dispatch three non-overlapping owners per design.md. Root owns integration, task records and documentation.
2. Backend: capability/schema + validation, providers, markup queue/recovery/restore, metadata/persistence + rates. Test actual upstream JSON/multipart, admission errors, old compatibility and lifecycle cleanup.
3. Media: safe SVG helpers, bounded thumbnail render, GIF frame validation/preservation, file routes, archives/sync and dependencies. Notify backend when helper API is ready. Test adversarial SVG/no network, malformed frames, original byte preservation and roundtrip.
4. Frontend: model/preset selectors, conditional fields, compatibility feedback, uploaded-markup overlay and source identity/retry, media UI gating, API types/locales and unit/browser tests. No drawing canvas.
5. Root: integrate owner reports/diff, README EN/zh-CN/zh-TW + changelog, update relevant Trellis specs. Run acceptance after integration; return defects to owner. Review original scope, not only passing tests.
6. Main final verification: .venv/bin/python -m pytest backend/tests -q (or scoped suites plus all relevant contracts if environmental limits); npm --prefix frontend run check; npm --prefix frontend run test:unit; npm --prefix frontend run build; targeted Playwright image-26 + existing image-25, edit/gallery/responsive critical cases. Inspect generated desktop/mobile screenshots using view_image. Test SVG/GIF actual binary/MIME/download/archives, not only mocked strings.
7. Do not use real API keys, paid calls or live user data. No push/deploy. Preserve pre-existing untracked Trellis/tool files. Record exact tests/failures and user-visible limitations. Finish only when required features and decisive checks are complete; commit only if standing workflow authorization and sandbox permit a scoped commit.
