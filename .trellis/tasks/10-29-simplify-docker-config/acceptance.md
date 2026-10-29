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
