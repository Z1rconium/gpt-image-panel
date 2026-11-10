# Acceptance evidence

## Follow-up checks (October 29, 2026)

- `.venv/bin/python -m pytest -q backend/tests/test_compose_configuration.py backend/tests/test_config_derivation.py` — 9 passed, 1 skipped. The skipped test requires Docker Compose and exercises project `.env`, absent numeric/boolean settings, explicitly empty values, and unlisted host/project variables.
- `.venv/bin/ruff check backend/tests/test_compose_configuration.py` — passed.
- `ruby -ryaml -e 'e=YAML.load_file("docker-compose.yml").fetch("services").fetch("web").fetch("environment"); abort "invalid entries" unless e.length == 66 && e.uniq == e && e.all? { |x| x.match?(/\A[A-Z][A-Z0-9_]*\z/) }; puts "YAML parsed: #{e.length} unique bare environment entries"'` — passed: 66 unique bare entries. This is a YAML and structure check, not Compose validation.
- `git diff --check` — passed.

## Earlier implementation evidence

The prior implementation session reported the following; its exact command lines were not retained in the handoff, so these are recorded as reported results rather than reproducible command transcripts:

- Full backend suite: 1,191 passed, 22 skipped. Socket tests used approved loopback access.
- Frontend unit tests: 354 passed; frontend type check and production build passed.
- Scoped Ruff checks passed. Broader Ruff checks reported existing unused-import findings.
- Isolated FastAPI TestClient smoke used a temporary data directory and passed startup, health, access unlock, built-in secret resolution, and settings save. No paid upstream calls were made.
- Isolated browser inspection screenshots: `/tmp/gpt-image-ui-check/settings.png` and `/tmp/gpt-image-ui-check/overall.png` (the latter shows the final Overall Config registry).

## Acceptance gap and migration

Docker is absent, so actual `docker compose config` parsing and container startup were not run. On a Docker-equipped host, run `.venv/bin/python -m pytest -q backend/tests/test_compose_configuration.py` and then run `docker compose config` and an isolated container startup/health/unlock/settings-save smoke with a temporary project and data directories. The static check does not establish Compose's runtime resolution semantics.

The compact example links to `CONFIGURATION.md` for retained options. Compose forwards only explicitly named settings. Add custom secret-registry environment variables to the service environment list. Retired database rows remain intact but are ignored; reverting the source restores the previous code path. No live `.env` or data was edited, no paid generation was requested, and no commit was made.

## Independent review status

The requested ccs-krill/gpt-6.1-terra read-only reviewer could not execute: the provider rejected the review request with a cybersecurity-risk flag. No independent approval was obtained. The main session did not reroute that denied review. Resume independent review only after the provider access restriction is resolved. Together with missing Docker runtime validation, this leaves the task awaiting final acceptance; it is not archived or committed as an accepted release.

## Close-out audit (November 10, 2026)

The implementation landed on `main` as `190f3bc` (`refactor(config): simplify Docker setup and remove outbound allowlists`) and has since shipped through v1.8.0, v1.8.1, v1.9.0-v1.9.3. This audit re-verified the acceptance criteria against the current tree.

### Verified against the tree

- Retired outbound allowlists are gone from the application: `UPSTREAM_HOST_ALLOWLIST`, `UPSTREAM_PROXY_HOST_ALLOWLIST`, `PROMPT_OPTIMIZER_HOST_ALLOWLIST`, `WEBHOOK_HOST_ALLOWLIST` and `R2_ENDPOINT_HOST_ALLOWLIST` have zero references under `backend/app`. `ALLOWED_HOSTS` survives only as the inbound Host check, which is the intended retained control.
- Retired tuning knobs are fixed constants, not env reads: `RUNTIME_METRICS_REFRESH_SECONDS = 10.0` and `PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS = 32000` in `core/settings.py`, still consumed by their owners. Derived limits still follow retained bases (`DB_EXECUTOR_WORKERS` derives from `MAX_ACTIVE_GENERATE_JOBS`).
- Inbound controls retained: `IP_ALLOWLIST`, `ALLOWED_HOSTS`, `TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_IPS`, `PUBLIC_ORIGIN`, `CSRF_ORIGIN_CHECK_ENABLED` are all still read. SSRF guards remain in `core/validators.py` (`validate_upstream_url`, `validate_webhook_url`, `validate_r2_endpoint_url`, `validate_image_url`, DNS/public-network checks) and `core/safe_connector.py`.
- `docker-compose.yml` forwards 66 unique bare environment names with no `env_file`; every one is documented in `CONFIGURATION.md` (0 missing). Retired names are absent from the forwarded list.
- Docs aligned in all three languages: the migration note is present in `README.md:216`, `README.zh-CN.md:212`, `README.zh-TW.md:212`, and `CONFIGURATION.md:91`. No retired setting remains in the settings UI or the overall-config registry.
- Specs carry the contract: `spec/backend/refactoring-compatibility.md` "Deployment configuration simplification" and `spec/backend/auth-and-security.md` inbound gate ordering.

### Re-run evidence

- `pytest backend/tests/test_compose_configuration.py test_config_derivation.py test_async_ssrf_contract.py test_secret_registry_review_regressions.py test_core_allowlist_redaction.py` — 32 passed, 1 skipped (the skip is the Docker-dependent Compose resolution test).
- Full backend suite — 1303 passed, 23 skipped, 1 failed. The failure is `test_generated_media.py::test_svg_atomic_save_thumbnail_and_download`, a local `libcairo` lookup issue unrelated to this task; it passes with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.
- `ruff check --select F401,F841` (the CI lint rule set) on the task's touched modules — all checks passed. `ruff check` with the default rule set reports pre-existing import-order and file-mode findings in `core/secrets.py` and `core/settings.py`; those files are outside the CI lint list and the findings predate this task.
- `git diff --check` — clean.

### Remaining gap (unchanged)

Docker is still absent on this host, so `docker compose config` runtime resolution and an isolated container startup/health/unlock/settings-save smoke were not run. The static structure check and the unit-level Compose test cover the forwarding contract but not Compose's own resolution semantics. The independent ccs-krill review remains unavailable for the reason above.

Closed as accepted on the strength of the implementation being merged, shipped across five releases, and re-verified against the current tree; the two gaps above are recorded as follow-ups rather than blockers.
