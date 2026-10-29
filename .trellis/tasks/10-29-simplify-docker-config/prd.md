# Simplify Docker deployment

User-approved implementation. Basic setup requires access password, upstream URL/key, and explicit HTTP cookie setting when applicable. Remove all outbound host allowlists, including upstream, proxy, optimizer/assistant, webhooks and R2. Retain inbound controls, URL/public-network checks, secret purpose/origin binding, existing data and mounts.

Remove low-value environment and runtime configuration knobs; keep deployment/business settings. Preserve old database rows without applying retired settings. Update English, simplified and traditional Chinese docs and settings UI. Never modify user .env or live data. No paid generation calls.

Acceptance: minimal configuration boots; health/unlock/settings save work; public custom endpoints work without lists; invalid/private URLs and mismatched secret bindings remain rejected. Retired env/database settings cannot override constants. Derived values follow retained bases. Compose parses; affected tests/lint/type checks pass; inspect changed UI. Report migration and any unrun checks.
