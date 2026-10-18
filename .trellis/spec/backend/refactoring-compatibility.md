# Compatibility surfaces during backend cleanup

## Scope

Use when removing imports, deduplicating helpers or extracting implementations. Application callers, facades and test seams can consume a module attribute that its own implementation never reads.

## Established entry points

```python
from backend.app.main import app, proxy
from backend.app.repositories.db import (
    _normalize_gallery_favorite,
    _normalize_prompt_snippet_favorite,
    _like_prompt_snippet_query,
)
from backend.app.repositories.db.rows import _like_contains_param
```

`api.contract_app.proxy` feeds the main entry point. `services.assistant_batch.assistant_client` is also an established test seam: `tests.support.contract` exposes that service as `assistant_router`, and assistant contract tests patch its `assistant_client` attribute. Preserve these names even when an unused-import check flags them.

## Contracts

- Preserve established names/signatures and facade exports when two helpers share one implementation. Callers retain their existing import paths.
- Check module-attribute accesses, fixture imports, reflective references, registrations and import-time effects before deleting an unused import.
- Keep favorite normalization distinct from tri-state filter normalization. Keep SQL LIKE escaping order and wildcard wrapping unchanged.
- A dead method on an internal data carrier may be removed while retaining the live class, constructor fields and defaults. R2 sync uses batched LIST/HEAD lookup through `RemoteKeyLookup.keys`, `etags` and `use_head_fallback`.

## Validation matrix

| Cleanup | Required evidence |
|---|---|
| Internal helper deletion | No caller/export/registration; surviving operation contracts pass |
| Helper delegation | Both old names preserve edge values and return semantics |
| Copied import removal | Attribute consumers and initialization effects screened; affected API/architecture tests pass |
| Import flagged by Ruff but used through another module | Retain the alias and its existing consumer |

## Good, base and bad cases

Good: `_like_prompt_snippet_query` delegates to the existing LIKE escaping primitive under the same name.

Base: delete an unexported helper with no caller while retaining the active streaming implementation.

Bad: remove `assistant_batch.assistant_client` or `contract_app.proxy` solely because Ruff reports F401.

## Required checks

Use `test_architecture_boundaries.py`, affected assistant/gallery/R2 contracts and the helper characterization tests. The global F401/F841/F811 baseline contains intentional facade and fixture exports; use scoped checks and current consumer evidence to decide each deletion.

## Wrong and correct

```python
# Wrong: replace the established favorite parser with a truthiness conversion.
return bool(value)

# Correct: preserve the established helper and its full input vocabulary.
return _normalize_gallery_favorite(value)
```
