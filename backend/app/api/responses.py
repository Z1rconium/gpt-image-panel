"""Adapters that turn service results into framework responses."""

from fastapi import Response
from fastapi.responses import StreamingResponse

from ..core.streaming import EmptyResponse, StreamedBody


def streaming_response(body: StreamedBody) -> StreamingResponse:
    return StreamingResponse(
        body.chunks,
        media_type=body.media_type,
        headers=body.headers,
    )


def empty_response(body: EmptyResponse) -> Response:
    return Response(
        status_code=body.status_code,
        media_type=body.media_type,
        headers=body.headers,
    )
