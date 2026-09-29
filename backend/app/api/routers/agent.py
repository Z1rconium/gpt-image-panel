"""Agent conversation HTTP request and response mapping."""

from fastapi import APIRouter, Header, Query, Request, Response

from ..responses import streaming_response
from ...core import security as auth
from ...schemas.agent import (
    AgentConversationCreateRequest,
    AgentConversationDetail,
    AgentConversationListResponse,
    AgentConversationRenameRequest,
    AgentConversationSummary,
    AgentTurnAccepted,
    AgentTurnRequest,
    AgentTurnStatus,
)
from ...schemas.common import MessageResponse
from ...services import agent_conversations, agent_stream

router = APIRouter()


@router.get("/api/agent/conversations", response_model=AgentConversationListResponse)
async def list_agent_conversations():
    return await agent_conversations.list_conversations()


@router.post(
    "/api/agent/conversations",
    response_model=AgentConversationSummary,
    status_code=201,
)
async def create_agent_conversation(req: AgentConversationCreateRequest):
    return await agent_conversations.create_conversation(req)


@router.get("/api/agent/conversations/{conversation_id}", response_model=AgentConversationDetail)
async def get_agent_conversation(
    conversation_id: str,
    before_seq: int | None = Query(default=None, ge=1),
    limit: int = Query(default=agent_conversations.DEFAULT_DETAIL_LIMIT, ge=1, le=agent_conversations.MAX_DETAIL_LIMIT),
):
    return await agent_conversations.get_conversation_detail(
        conversation_id, before_seq=before_seq, limit=limit
    )


@router.patch("/api/agent/conversations/{conversation_id}", response_model=AgentConversationSummary)
async def rename_agent_conversation(conversation_id: str, req: AgentConversationRenameRequest):
    return await agent_conversations.rename_conversation(conversation_id, req)


@router.delete("/api/agent/conversations/{conversation_id}", response_model=MessageResponse)
async def delete_agent_conversation(conversation_id: str):
    await agent_conversations.delete_conversation(conversation_id)
    return MessageResponse(status="success", message="Agent conversation deleted")


@router.post(
    "/api/agent/conversations/{conversation_id}/turns",
    response_model=AgentTurnAccepted,
    status_code=202,
)
async def start_agent_turn(conversation_id: str, req: AgentTurnRequest, response: Response):
    accepted = await agent_conversations.start_turn(conversation_id, req)
    if accepted.replayed:
        response.status_code = 200
    return accepted


@router.get("/api/agent/turns/{turn_id}", response_model=AgentTurnStatus)
async def get_agent_turn(turn_id: str):
    return await agent_conversations.get_turn_status(turn_id)


@router.post("/api/agent/turns/{turn_id}/cancel", response_model=AgentTurnStatus, status_code=202)
async def cancel_agent_turn(turn_id: str):
    return await agent_conversations.cancel_turn(turn_id)


@router.get("/api/agent/turns/{turn_id}/events")
async def stream_agent_turn_events(
    turn_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None),
):
    cursor = after
    # Only short digit strings are usable cursors; longer ones would raise on int().
    if last_event_id and last_event_id.isdigit() and len(last_event_id) <= 18:
        cursor = max(cursor, int(last_event_id))
    body = await agent_stream.stream_turn_events(
        turn_id=turn_id,
        after=cursor,
        client_ip=auth.get_client_ip(request),
        is_disconnected=request.is_disconnected,
    )
    return streaming_response(body)
