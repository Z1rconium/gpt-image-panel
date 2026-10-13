# Coordination implementation

Dependency: complete database-runtime import migration before final backend review.

- [x] Enumerate public/used-private symbols and monkeypatch consumers.
- [x] Extract five domains with unchanged bodies/direct implementation imports.
- [x] Create explicit facade and verify operation signatures/owners.
- [x] Cover only uncovered compatibility/ownership behavior with focused tests.
- [x] Run .venv/bin/python -m pytest backend/tests/test_storage_migrations_contract.py backend/tests/test_sqlite_coordination_contract.py backend/tests/test_worker_distribution_contract.py backend/tests/test_access_settings_contract.py backend/tests/test_agent_boundaries.py backend/tests/test_gallery_job_sse.py backend/tests/test_assistant_contract.py backend/tests/test_nodeimage_upload_contract.py -q
- [x] Run architecture checks and inspect SQL/body diff.
- [x] Integrated full backend suite (945 passed, 22 skipped in one loopback-permitted run).
- [x] Implementation ownership/validation record (`validation.md`).
- [x] Independent Trellis check (reviewer: 65 focused tests passed; no extraction issues).

Ownership: repositories/coordination package extraction and focused tests. No runtime/frontend modifications.
