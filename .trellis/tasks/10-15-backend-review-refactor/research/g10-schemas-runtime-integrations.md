# G10 schemas + runtime + integrations (non-upstream) review — baseline a50da7a

## Modules reviewed
| Module | Lines | Notable |
|---|---|---|
| schemas/settings.py | 587 | settings + presets + provider mapping + overall config DTOs |
| schemas/provider/v1.py / v2.py | 116 / 370 | versioned provider mapping models |
| schemas/{access,agent,assistant,common,gallery,generation,nodeimage,snippets}.py, schemas/provider/{_common,resolved}.py | — | — |
| runtime/blocking.py / state.py | — | executor pools |
| integrations/session_pool.py (168), json_client.py (98), agent_client.py, assistant_client.py, prompt_optimizer_client.py, agent_parsers.py, agent_types.py, agent_search.py, turnstile.py, nodeimage/client.py, r2/{sync,config,client}.py | — | — |

## Runtime/session verification (10-13 + prior)
Held and clean:
- `runtime/*` imports no `repositories` (layer check 0 violations); executor accounting intact.
- **Session pooling is well-factored**: every HTTP client obtains a shared session via `integrations/session_pool.get_pool()` — `generation.py:147`, `agent_client.py:300`, `json_client.py:56`, `nodeimage/client.py:196`, `turnstile.py:35`, `transport.py`. No per-request `aiohttp.ClientSession` outside `session_pool.py:131`.

## Findings

### F-G10-1 — P3, improvement: schemas/settings.py aggregates many domains in one DTO module
- Evidence: `backend/app/schemas/settings.py` holds settings request/response (`SettingsRequest:82`, `SettingsResponse:176`), preset DTOs (`ApiPresetResponse:26`, `PresetCreateRequest:45`, export/import preview `PresetExportPackage:241`, `PresetImportPreviewResponse:268`), provider-mapping validate DTOs (`ProviderMappingValidateResponse:319`), R2 health (`R2HealthResponse:330`), and overall-config DTOs (`OverallConfigItem:334`, `OverallConfigUpdateRequest:372`).
- Impact: every settings-adjacent feature adds classes here, while gallery/agent/assistant have per-domain schema modules; inconsistent granularity.
- Remedy sketch: split `settings`, `presets`, `provider_mapping`, `overall_config` DTO modules.
- Effort/risk: low / low (mostly imports).

### F-G10-2 — P3, improvement (caution): provider v1/v2 mapping models duplicate field validators
- Evidence: `schemas/provider/v1.py` (`ProviderSubmit:18`, `ProviderPoll:53`, `ProviderResult:72`, `ProviderCancel:92`, `ProviderConfig:102`) vs `schemas/provider/v2.py` (`ProviderSubmitV2:23`, …, `ProviderConfigV2:318`) repeat `validate_url_path`/`validate_query`/`validate_status_path`/`validate_template` per class; v2 is a superset (templates, transports, edit protocol, capabilities).
- Impact: validation rules are maintained twice.
- **Caution**: two provider mapping versions read as a deliberate compatibility contract (v1 = URLs from upstream responses; v2 = templated URLs). Do **not** consolidate unless v1 can be retired; treat as follow-up only if a v1 deprecation plan exists.
- Effort/risk: medium / high (compatibility surface).

### F-G10-3 — P3, improvement: r2/config vs core/secrets env handling
- Evidence: `integrations/r2/config.py` (env-backed R2 config) overlaps `core/secrets.py` credential-env handling; not a defect but a second env/secret read path.
- Remedy sketch: route R2 credential reads through `core/secrets`.
- Effort/risk: low / low.

## Coverage notes
- `json_client.post_json` is the shared JSON-over-HTTP helper for OpenAI-compatible calls; `agent_client.stream_agent_response` (115 lines, nest 6) legitimately differs (streaming) and is not duplication.
- `agent_types.py` vs `schemas/agent.py` have adjacent names but distinct roles (integration-internal types vs API DTOs); no overlap proven, noted.
- No async blocking: boto3/R2 and file work run via `runtime/blocking`; DNS resolution has an async variant used on request paths.
