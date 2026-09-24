"""Public service API for masked edit paste-back processing.

The implementation lives beside the upstream response pipeline so the
integration layer keeps its dependency direction toward core modules.
"""

from ..integrations.upstream.edit_paste_back import PasteBackOutcome, paste_back_image

__all__ = ["PasteBackOutcome", "paste_back_image"]
