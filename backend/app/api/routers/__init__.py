from . import (
    access,
    agent,
    assistant,
    edits,
    gallery,
    generate,
    metrics,
    prompt,
    prompt_snippets,
    settings,
    settings_health,
    settings_presets,
    static,
)


routers = (
    access.router,
    agent.router,
    assistant.router,
    settings.router,
    settings_presets.router,
    settings_health.router,
    generate.router,
    edits.router,
    gallery.router,
    prompt_snippets.router,
    prompt.router,
    metrics.router,
    static.router,
)
