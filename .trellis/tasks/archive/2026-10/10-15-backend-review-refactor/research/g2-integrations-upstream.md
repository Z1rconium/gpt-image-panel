# G2 integrations/upstream review — baseline a50da7a

## Modules reviewed
| Module | Lines | Notable functions |
|---|---|---|
| integrations/upstream/generation.py | 1076 | call_image_generation_api(170), call_image_edit_api(161), consume_streaming_image_response(153,nest 8), save_gallery_entries_from_upstream_data(147) |
| integrations/upstream/async_provider.py | 1069 | _submit_task(167), run_async_provider(121) |
| integrations/upstream/transport.py | 520 | parse_upstream_chat_completion_response(72), spool_limited_response(61) |
| integrations/upstream/payloads.py, contracts.py, edit_paste_back.py, chroma.py, diagnostics.py, errors.py | — | — |

## Findings

### F-G2-1 — P2, improvement: gallery-entry construction/download orchestration lives in the upstream client module
- Evidence: `backend/app/integrations/upstream/generation.py:155 save_gallery_entries_from_upstream_data` (147 lines, `:155-301`) builds gallery metadata, image ids/filenames, per-task byte budgets, calls `detect_image_format`, `validate_image_header_bytes`, and drives `download_decode`/`validate` stages.
- Not a layer *import* violation: persistence is injected via the `persist_gallery_entry` callback (`:165`), and the layer check reports 0 violations. It is a **boundary/naming** smell: an integrations-layer function whose name and half its body are a services concern.
- Impact: gallery asset policy (filename/format/size budget) is fragmented between integrations and services; hard to test the upstream transport without dragging gallery rules in.
- Remedy sketch: keep pure upstream call/parse in `generation.py`; move entry+asset construction into a services module that calls it.
- Effort/risk: medium / medium (touches the generation and edit paths).

### F-G2-2 — P2, improvement: four ~150-line orchestration functions concentrated in generation.py
- Evidence: `generation.py:684 call_image_generation_api` (170), `:915 call_image_edit_api` (161), `:304 consume_streaming_image_response` (153, **nesting depth 8** at `:304-456`), `:155 save_gallery_entries_from_upstream_data` (147).
- Impact: the streaming parse path and non-streaming path each carry their own buffering/decode/frame handling with deep nesting; the two `call_*` functions duplicate a large request/response envelope.
- Remedy sketch: extract a small streaming decoder + a shared "call and normalize response" helper; flatten `consume_streaming_image_response`.
- Effort/risk: medium / medium.
- Caveat: `async_provider.py` (`_submit_task` 167) is a separate submit/poll/result driver with its own long functions — its shape matches a state machine and was not flagged as duplication, only noted.

## Coverage notes
- `async_provider.py` is a coherent submit/poll/result driver for declaratively-mapped providers; helpers are mostly small (build/render/resolve), with only `_submit_task` and `run_async_provider` long. Not reported separately.
- `transport.py` (520) mixes response reading, SSE frame decoding, URL asset download, and JSON parse/classification; each function is bounded (≤72 lines). Borderline grab-bag but no defect shown — not reported.
- Layer matrix: 0 violations (top-level and deferred).
