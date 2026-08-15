"""Thread routes (§4.3). Read routes need only WORKSPACE_MEMBER (any
member, including Viewer); mutations additionally require CREATE_MESSAGES
— a thread is just a container for messages, so gating its lifecycle
behind the same capability as sending a message avoids a Viewer creating
threads they can never post to, without inventing a new capability."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from aether.app.threads.create_thread import CreateThreadCommand
from aether.app.threads.delete_thread import DeleteThreadCommand
from aether.app.threads.get_thread import GetThreadCommand
from aether.app.threads.list_messages import ListMessagesCommand
from aether.app.threads.list_threads import ListThreadsCommand
from aether.app.threads.update_thread import UpdateThreadCommand
from aether.domain.entities import Message, Thread
from aether.domain.policy import CREATE_MESSAGES
from aether.http.authz import AuthRequirement, require_capability, route_auth
from aether.http.composition import WorkspaceScope
from aether.http.deps import get_workspace_scope
from aether.http.pagination import (
    DEFAULT_PAGE_LIMIT,
    clamp_limit,
    decode_created_at_cursor,
    decode_seq_cursor,
    encode_created_at_cursor,
    encode_seq_cursor,
)
from aether.http.rate_limit_deps import RateLimitClass, rate_limit_by_user
from aether.http.schemas.threads import (
    CreateThreadRequest,
    MessageListResponse,
    MessageResponse,
    ThreadListResponse,
    ThreadResponse,
    UpdateThreadRequest,
)

router = APIRouter(prefix="/v1", tags=["threads"])


def _to_thread_response(thread: Thread) -> ThreadResponse:
    return ThreadResponse(
        id=thread.id,
        workspace_id=thread.workspace_id,
        created_by=thread.created_by,
        title=thread.title,
        settings=thread.settings,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _to_message_response(message: Message) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        workspace_id=message.workspace_id,
        thread_id=message.thread_id,
        seq=message.seq,
        role=message.role,
        content=message.content,
        status=message.status,
        client_message_id=message.client_message_id,
        model=message.model,
        prompt_tokens=message.prompt_tokens,
        completion_tokens=message.completion_tokens,
        cost_microcents=message.cost_microcents,
        grounded=message.grounded,
        created_at=message.created_at,
    )


@router.post(
    "/workspaces/{workspace_id}/threads",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def create_thread(
    workspace_id: UUID,
    body: CreateThreadRequest,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> ThreadResponse:
    require_capability(scope.caller_membership.role, CREATE_MESSAGES)
    thread = await scope.create_thread.execute(
        CreateThreadCommand(
            workspace_id=workspace_id, created_by=scope.caller_membership.user_id, title=body.title
        )
    )
    return _to_thread_response(thread)


@router.get(
    "/workspaces/{workspace_id}/threads",
    response_model=ThreadListResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def list_threads(
    workspace_id: UUID,
    scope: WorkspaceScope = Depends(get_workspace_scope),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT),
) -> ThreadListResponse:
    page_limit = clamp_limit(limit)
    after = decode_created_at_cursor(cursor) if cursor is not None else None
    threads = await scope.list_threads.execute(
        ListThreadsCommand(workspace_id=workspace_id, after=after, limit=page_limit + 1)
    )
    has_more = len(threads) > page_limit
    page = threads[:page_limit]
    next_cursor = encode_created_at_cursor(page[-1].created_at, page[-1].id) if has_more else None
    return ThreadListResponse(items=[_to_thread_response(t) for t in page], next_cursor=next_cursor)


@router.get(
    "/workspaces/{workspace_id}/threads/{thread_id}",
    response_model=ThreadResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def get_thread(
    workspace_id: UUID, thread_id: UUID, scope: WorkspaceScope = Depends(get_workspace_scope)
) -> ThreadResponse:
    thread = await scope.get_thread.execute(
        GetThreadCommand(workspace_id=workspace_id, thread_id=thread_id)
    )
    return _to_thread_response(thread)


@router.patch(
    "/workspaces/{workspace_id}/threads/{thread_id}",
    response_model=ThreadResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def update_thread(
    workspace_id: UUID,
    thread_id: UUID,
    body: UpdateThreadRequest,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> ThreadResponse:
    require_capability(scope.caller_membership.role, CREATE_MESSAGES)
    thread = await scope.update_thread.execute(
        UpdateThreadCommand(workspace_id=workspace_id, thread_id=thread_id, title=body.title)
    )
    return _to_thread_response(thread)


@router.delete(
    "/workspaces/{workspace_id}/threads/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def delete_thread(
    workspace_id: UUID, thread_id: UUID, scope: WorkspaceScope = Depends(get_workspace_scope)
) -> None:
    require_capability(scope.caller_membership.role, CREATE_MESSAGES)
    await scope.delete_thread.execute(
        DeleteThreadCommand(workspace_id=workspace_id, thread_id=thread_id)
    )


@router.get(
    "/workspaces/{workspace_id}/threads/{thread_id}/messages",
    response_model=MessageListResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def list_messages(
    workspace_id: UUID,
    thread_id: UUID,
    scope: WorkspaceScope = Depends(get_workspace_scope),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT),
) -> MessageListResponse:
    page_limit = clamp_limit(limit)
    after_seq = decode_seq_cursor(cursor) if cursor is not None else None
    messages = await scope.list_messages.execute(
        ListMessagesCommand(
            workspace_id=workspace_id,
            thread_id=thread_id,
            after_seq=after_seq,
            limit=page_limit + 1,
        )
    )
    has_more = len(messages) > page_limit
    page = messages[:page_limit]
    next_cursor = encode_seq_cursor(page[-1].seq) if has_more else None
    return MessageListResponse(
        items=[_to_message_response(m) for m in page], next_cursor=next_cursor
    )
