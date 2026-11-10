# Execution

1. Implementation owner inventories every setting into retained/retired/derived in configuration-inventory.md before edits, applying design rules. Read relevant specs and current diff.
2. Implement coherent backend/config/secret/validator callers, deployment examples, UI metadata, multilingual docs and tests. Preserve other owners' changes. No commit or release.
3. Run targeted config, binding, SSRF, proxy, R2, webhook, generation/assistant tests; project lint/type checks; Compose parse and isolated startup/health/unlock/save smoke without paid requests. Inspect changed UI.
4. Independent same-model check agent reviews original contract and candidate; fixes returned to implementation owner. Main integrates acceptance evidence and updates specs.

Required model: ccs-krill/gpt-6.1-terra; do not substitute if unavailable. Main owns task records/specs; implementation owner owns application/deployment/docs/tests. Preserve legacy database rows for rollback; rollback via reverting source changes, no data migration.
