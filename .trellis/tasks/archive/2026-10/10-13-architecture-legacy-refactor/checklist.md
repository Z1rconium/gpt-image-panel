# Final integration checklist

## Acceptance criteria

- [x] AC1 / R1: runtime owns generic workers only; DB adapter owns SQLite scopes/retries/shutdown; cancellation and submission rejection accounting tested.
- [x] AC1 / R1: all moved DB helper consumers migrated; no runtime-to-repository imports, including deferred imports; layer-matrix regression enforced.
- [x] AC2 / R2: five coordination domains own their policies; 35 facade signatures and all 43 original function implementations preserved.
- [x] AC2 / R2: SQL, atomic admission, cancellation, lease fencing and expiration semantics covered by unchanged source and passing contracts.
- [x] AC3 / R3: obsolete sources and list requests cannot mutate state or restart polling; request/event ordering, cleanup, visibility and explicit reuse covered.
- [x] AC3 / R3: state/equality/history helpers and public facade behavior preserved; notification, selection and tracked-preview integration covered.
- [x] AC4 / R4: complete backend run passes (945 passed / 22 configured skips); deterministic DNS fixture issue reproduced and minimally fixed.
- [x] AC4 / R4: security/SSRF/provider/architecture subset passes (96 tests); CI/scoped F401,F841 lint and diff whitespace checks pass.
- [x] AC4 / R4: frontend diagnostics (0 errors/warnings), 206 unit tests, build, unchanged bundle budget and Worker checks pass.
- [x] AC4 / R4: focused browser flows pass (29 tests / 3 existing opt-in performance skips); environment limitations recorded.
- [x] AC5 / R5: source-backed audit distinguishes confirmed defects from design opportunities; scoped DB/coordination/jobs contracts match implementation.
- [x] AC5 / R5: pre-existing historyLoading cleanup behavior, broad lint debt, UI follow-ups and separate Bootstrap Guidelines work remain explicitly scoped/deferred.

## Main-session handoff

- [x] Main session verified each staged file set against the approved commit plan and preserved unrelated files.
- [x] Main session created the five authorized work commits; archive and journal execution follow before pushing.

These final handoff steps are deliberately not performed by the integration reviewer. Exact validation commands and outcomes are in `validation.md`.
