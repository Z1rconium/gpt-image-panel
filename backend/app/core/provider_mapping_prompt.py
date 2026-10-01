"""LLM prompt for authoring a declarative provider mapping.

The prompt describes the v1/v2 provider_config contract, the supported template
variables, the JSONPath subset, and the credential rules. It is built from the
runtime constants so the prompt and the validator cannot drift apart.
"""

from .provider_mapping import TEMPLATE_VARIABLES

_VARIABLE_DESCRIPTIONS = {
    "prompt": "the prompt text after prompt-guard/chroma post-processing",
    "model": "the requested model name",
    "n": "number of images in this unit (always 1 per queue unit)",
    "width": "requested width in pixels (omitted when size is auto)",
    "height": "requested height in pixels (omitted when size is auto)",
    "size": "requested size string like 1024x1024 (omitted when size is auto)",
    "quality": "requested quality value",
    "output_format": "requested output format (png/jpeg/webp)",
    "background": "requested background value",
}


def build_provider_mapping_prompt() -> str:
    variables = "\n".join(
        f"- {{{{ {name} }}}} - {_VARIABLE_DESCRIPTIONS.get(name, 'supported variable')}"
        for name in sorted(TEMPLATE_VARIABLES)
    )
    return f"""You are writing a `provider_config` JSON mapping for GPT Image Panel.

The panel submits one image-generation task per queue unit and then polls the
provider until the task finishes. Your mapping tells the panel which HTTP
requests to send and how to read the responses. Respond with a single JSON
object and nothing else. Never ask for or include an API key: the key is stored
by the panel and injected separately.

## Template variables usable in submit.body values

{variables}

A value that is exactly one placeholder keeps its JSON type (for example
"n": "{{{{n}}}}" becomes the number 1). A placeholder inside a longer string is
substituted as text. If a variable has no value for a request (for example
width/height with size=auto) and it is used as a whole value, the field is
omitted from the body. Placeholders are not allowed in JSON keys.

## Version 2 mapping shape (preferred)

{{
  "version": 2,
  "auth": {{"header": "Authorization", "scheme": "Bearer"}},
  "submit": {{
    "path": "/v1/jobs",
    "body": {{"prompt": "{{{{prompt}}}}", "model": "{{{{model}}}}"}},
    "idempotency_header": "Idempotency-Key"
  }},
  "poll": {{
    "task_id_path": "$.data.task_id",
    "url_template": "/v1/jobs/{{{{task_id}}}}",
    "status_path": "$.data.status",
    "done": ["succeeded"],
    "failed": ["failed", "cancelled"],
    "interval_seconds": 2,
    "timeout_seconds": 600
  }},
  "result": {{
    "task_id_path": "$.data.task_id",
    "url_template": "/v1/jobs/{{{{task_id}}}}/result",
    "images_path": "$.data.images[*].url",
    "image_kind": "url"
  }},
  "cancel": {{
    "task_id_path": "$.data.task_id",
    "url_template": "/v1/jobs/{{{{task_id}}}}/cancel",
    "method": "DELETE"
  }}
}}

Rules for the v2 fields:
- `poll` must have either `url_path` (a JSONPath into the submit response that
  holds a full status URL) or `task_id_path` plus `url_template`.
- `poll.url_template`, `result.url_template` and `cancel.url_template` are plain
  paths starting with "/" that must contain {{{{task_id}}}}; the task id is
  percent-encoded as one path segment. Absolute URLs are rejected.
- `result.images_path` selects image strings (URLs or base64) from the final
  response. If `result` is omitted entirely the panel reads images from the last
  poll response. `result.image_kind` is "url" or "b64_json".
- `result.task_id_path`/`url_template` are optional; use them only when the
  result lives at a separate endpoint. `result.url_path` is the alternative for
  providers that return the result URL directly.
- `cancel` is optional and is only used for best-effort cancellation.
- `submit.idempotency_header` is optional; set it when the provider accepts a
  client idempotency key, so an interrupted submit can be retried safely.

## Version 1 mapping shape (also accepted)

Same as version 2, but `poll.url_path`, `result.url_path` and `cancel.url_path`
are required JSONPath selectors that read full URLs from the responses, and
`submit` has no `idempotency_header`.

## JSONPath subset

Only `$.key`, `.key`, `[0]` and `[*]` are supported (for example
`$.data.items[*].image_url`). Quoted keys, filters, slices and recursive
descent are not supported. Keep paths under 200 characters and at most 12
tokens.

## URL and credential rules

- `submit.path` is a plain path appended to the preset API URL and may contain
  {{{{model}}}} only.
- Status/result/cancel URLs returned by the provider must be on the same origin
  as the preset API URL; cross-origin follow-ups are rejected.
- The mapping is limited to 16 KiB of JSON.

## What to do if the API documentation is incomplete

Do not guess. If the documentation does not state the submit path/body, the
task id location, the status values, the result shape, or the cancel endpoint,
list exactly which of those are missing and ask for them. If it does specify
them, output only the JSON mapping.

## Reminder

Never request, print, or embed an API key, token, or credential in the mapping.
The panel owns credentials separately.
"""


__all__ = ["build_provider_mapping_prompt"]
