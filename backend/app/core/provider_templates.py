"""Ready-made provider mappings offered by the settings UI.

Each mapping is plain data that must validate as a ``ProviderConfig``; a test
enforces that. They describe the provider's documented queue API, not
credentials: the key always comes from the preset.
"""

from typing import Any

FAL_QUEUE_TEMPLATE: dict[str, Any] = {
    "version": 1,
    "auth": {"header": "Authorization", "scheme": "Key"},
    "submit": {
        "path": "/{{model}}",
        "body": {
            "prompt": "{{prompt}}",
            "num_images": "{{n}}",
            "image_size": {"width": "{{width}}", "height": "{{height}}"},
        },
    },
    "poll": {
        "url_path": "$.status_url",
        "status_path": "$.status",
        "done": ["COMPLETED"],
        "failed": ["FAILED", "ERROR"],
        "interval_seconds": 2,
        "timeout_seconds": 600,
    },
    "result": {
        "url_path": "$.response_url",
        "images_path": "$.images[*].url",
        "image_kind": "url",
    },
    "cancel": {"url_path": "$.cancel_url", "method": "PUT"},
}

PROVIDER_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "fal",
        "name": "fal.ai (queue)",
        "api_url": "https://queue.fal.run",
        "default_model": "fal-ai/flux/dev",
        "config": FAL_QUEUE_TEMPLATE,
    },
]
