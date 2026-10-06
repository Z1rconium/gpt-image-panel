"""Agent conversations, turns, messages, image references and turn events.

The implementation is split by entity; this package re-exports the stable names.
"""
# ruff: noqa: F401  (re-export facade)

from ._common import (
    ACTIVE_TURN_STATUSES,
    AgentAttachmentError,
    AgentConversationLimitError,
    AgentLeaseLostError,
    AgentTurnConflictError,
    DEFAULT_TITLE_MAX_CHARS,
    TERMINAL_TURN_STATUSES,
    _CONVERSATION_SELECT_SQL,
    _IMAGE_SELECT_SQL,
    _MESSAGE_STATUS_FOR_TURN,
    _conversation_from_row,
    _image_from_row,
    _message_from_row,
    _new_id,
    _require_owner,
    _title_from_text,
    _turn_from_row,
)
from .conversations import (
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
)
from .branches import (
    _branch_text,
    _path_ids,
    branch_snapshot,
    path_turn_ids,
    select_branch,
)
from .turns import (
    LEGACY_IMAGE_WAIT_TIMEOUT_ERROR,
    claim_turn,
    create_turn,
    finish_turn,
    get_turn,
    get_turn_by_client_id,
    is_turn_cancel_requested,
    list_turns_needing_reconcile,
    renew_turn_lease,
    request_turn_cancel,
    set_turn_rounds_used,
    sweep_stale_turns,
)
from .messages import (
    get_image_by_label,
    get_message,
    insert_pending_output_image,
    list_conversation_images,
    list_messages,
    list_pending_images_for_turn,
    list_reconcilable_images_for_turn,
    set_image_job,
    settle_image,
    update_message_content,
)
from .events import (
    append_turn_event,
    append_turn_events,
    list_turn_events,
    purge_turn_events,
    read_event_cursor,
)
from ..db import _connect, _ensure_database, _transaction
