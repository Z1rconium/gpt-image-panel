# Design

Remove outbound allowlist arguments and metadata end-to-end, including credential startup dependencies. R2 custom public HTTPS hosts need no Cloudflare suffix. Preserve network and origin restrictions independently.

Keep business inputs (URLs/keys/models/routes/feature switches), inbound access settings, secret storage policy, paths/CDN/logging/cost rates, R2 schedule, upload size, generation concurrency/queue size and Granian workers.

Fix internal tuning at current backend defaults or existing derived formulas: connection pools, SQLite and executor tuning, leases/polling/progress/retention, metrics cadence, Agent internal limits, assistant preprocessing, streaming/cache, body timeouts/internal response limits/import protections/SSE limits/thumbnail and file concurrency, version timeout/cache/branch, webhook retry/timeout and R2 concurrency. Remove their env reads and overall-config write entries. Ignore retired persisted rows without deleting them. Preserve derived recomputation. Granian-native knobs remain supported upstream but leave the default example.

Compose explicitly passes supported settings, never bulk host env; optional absent values must not be injected as empty numeric/boolean overrides. Backend defaults are authoritative (optimizer output 32000, metrics cadence 10s). Example first screen has access key, upstream URL/key and cookie security; optional settings grouped below. No actual .env or data changes.
