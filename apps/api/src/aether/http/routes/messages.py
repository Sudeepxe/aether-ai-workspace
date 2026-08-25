"""POST .../messages (§4.3, §4.4): the SSE streaming surface, issue #26's
echo-proof and issue #27's cross-replica resume both live here.

Deliberately does **not** use ``get_workspace_scope`` — see
``get_chat_authorization``'s docstring for why a chat route can never
hold one DB connection open for a token stream's duration. Generation
always runs as an independent background task (``sse.run_generation_in_background``)
so a client disconnect never stops the assistant's reply from finishing
and being persisted; the HTTP response — for both a fresh stream and a
Last-Event-ID reconnect — is always ``sse.follow_buffer`` reading the
shared Redis buffer, never the orchestrator's generator directly.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from aether.app.chat.get_generation_status import GetGenerationStatusCommand
from aether.app.chat.send_message import SendMessageCommand
from aether.domain.entities import ApiKeyScope
from aether.domain.policy import CREATE_MESSAGES
from aether.http import sse
from aether.http.authz import AuthRequirement, route_auth
from aether.http.composition import Container
from aether.http.deps import (
    ChatPrincipal,
    get_chat_authorization,
    get_container,
    require_chat_scope,
)
from aether.http.rate_limit_deps import RateLimitClass, rate_limit_by_user_or_api_key
from aether.http.schemas.threads import SendMessageRequest

router = APIRouter(prefix="/v1", tags=["messages"])

_SSE_MEDIA_TYPE = "text/event-stream"


@router.post(
    "/workspaces/{workspace_id}/threads/{thread_id}/messages",
    response_model=None,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user_or_api_key(RateLimitClass.HEAVY))],
)
async def send_message(
    workspace_id: UUID,
    thread_id: UUID,
    body: SendMessageRequest,
    request: Request,
    container: Container = Depends(get_container),
    principal: ChatPrincipal = Depends(get_chat_authorization),
) -> StreamingResponse | JSONResponse:
    require_chat_scope(principal, CREATE_MESSAGES, ApiKeyScope.CHAT_WRITE)

    last_event_id = request.headers.get("Last-Event-ID")
    wants_stream = _SSE_MEDIA_TYPE in (request.headers.get("accept") or "")

    if last_event_id is not None:
        generation_id_str, _, seq_str = last_event_id.rpartition(":")
        generation_id = UUID(generation_id_str)
        after_seq = int(seq_str)
        # Raises GenerationNotFoundError (-> 404) if the buffer has
        # nothing for this generation — expired or never existed. The
        # documented client-side fallback (§5.2) is to reconcile from
        # the persisted message instead of retrying this endpoint.
        await container.get_generation_status.execute(
            GetGenerationStatusCommand(workspace_id=workspace_id, generation_id=generation_id)
        )
        return StreamingResponse(
            sse.follow_buffer(
                container.stream_buffer, workspace_id, generation_id, after_seq=after_seq
            ),
            media_type=_SSE_MEDIA_TYPE,
        )

    command = SendMessageCommand(
        workspace_id=workspace_id,
        thread_id=thread_id,
        content=body.content,
        client_message_id=body.client_message_id,
    )
    agen = container.send_message.execute(command)
    first_event = (
        await agen.__anext__()
    )  # always `meta`, seq 0 — already buffered by the time it's yielded
    generation_id = first_event.generation_id
    sse.run_generation_in_background(sse.drain(agen))

    if not wants_stream:
        return JSONResponse(status_code=202, content={"generation_id": str(generation_id)})

    return StreamingResponse(
        sse.follow_buffer(container.stream_buffer, workspace_id, generation_id, after_seq=-1),
        media_type=_SSE_MEDIA_TYPE,
    )
