"""Document routes (§4.3, FR-KB-5). Reads need only READ_KNOWLEDGE_BASE
(every role, including Viewer, already has it — the check is here for
parity with the resource catalog's "S,K" notation and to future-proof
against a lower role tier); mutations (:initiate, :confirm, delete)
require MANAGE_DOCUMENTS (Member+), matching "delete: role >= Member"
in the catalog.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from aether.app.documents.confirm_upload import ConfirmDocumentUploadCommand
from aether.app.documents.delete_document import DeleteDocumentCommand
from aether.app.documents.get_document import GetDocumentCommand
from aether.app.documents.initiate_upload import InitiateDocumentUploadCommand
from aether.app.documents.list_documents import ListDocumentsCommand
from aether.domain.entities import Document
from aether.domain.policy import MANAGE_DOCUMENTS, READ_KNOWLEDGE_BASE
from aether.http.authz import AuthRequirement, require_capability, route_auth
from aether.http.composition import WorkspaceScope
from aether.http.deps import get_workspace_scope
from aether.http.pagination import (
    DEFAULT_PAGE_LIMIT,
    clamp_limit,
    decode_created_at_cursor,
    encode_created_at_cursor,
)
from aether.http.rate_limit_deps import RateLimitClass, rate_limit_by_user
from aether.http.schemas.documents import (
    ConfirmUploadRequest,
    DocumentListResponse,
    DocumentResponse,
    InitiateUploadRequest,
    InitiateUploadResponse,
)

router = APIRouter(prefix="/v1", tags=["documents"])


def _to_document_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        workspace_id=document.workspace_id,
        filename=document.filename,
        mime=document.mime,
        size_bytes=document.size_bytes,
        status=document.status,
        failure_stage=document.failure_stage,
        failure_reason=document.failure_reason,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.post(
    "/workspaces/{workspace_id}/documents:initiate",
    response_model=InitiateUploadResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.HEAVY))],
)
async def initiate_document_upload(
    workspace_id: UUID,
    body: InitiateUploadRequest,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> InitiateUploadResponse:
    require_capability(scope.caller_membership.role, MANAGE_DOCUMENTS)
    result = await scope.initiate_document_upload.execute(
        InitiateDocumentUploadCommand(
            workspace_id=workspace_id,
            mime=body.mime,
            size_bytes=body.size_bytes,
            content_sha256=body.content_sha256,
        )
    )
    return InitiateUploadResponse(
        document_id=result.document_id,
        object_key=result.object_key,
        upload_url=result.upload_url,
        upload_fields=result.upload_fields,
    )


@router.post(
    "/workspaces/{workspace_id}/documents:confirm",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def confirm_document_upload(
    workspace_id: UUID,
    body: ConfirmUploadRequest,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> DocumentResponse:
    require_capability(scope.caller_membership.role, MANAGE_DOCUMENTS)
    document = await scope.confirm_document_upload.execute(
        ConfirmDocumentUploadCommand(
            workspace_id=workspace_id,
            document_id=body.document_id,
            filename=body.filename,
            mime=body.mime,
            size_bytes=body.size_bytes,
            content_sha256=body.content_sha256,
            object_key=body.object_key,
        )
    )
    return _to_document_response(document)


@router.get(
    "/workspaces/{workspace_id}/documents",
    response_model=DocumentListResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def list_documents(
    workspace_id: UUID,
    scope: WorkspaceScope = Depends(get_workspace_scope),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT),
) -> DocumentListResponse:
    require_capability(scope.caller_membership.role, READ_KNOWLEDGE_BASE)
    page_limit = clamp_limit(limit)
    after = decode_created_at_cursor(cursor) if cursor is not None else None
    documents = await scope.list_documents.execute(
        ListDocumentsCommand(workspace_id=workspace_id, after=after, limit=page_limit + 1)
    )
    has_more = len(documents) > page_limit
    page = documents[:page_limit]
    next_cursor = encode_created_at_cursor(page[-1].created_at, page[-1].id) if has_more else None
    return DocumentListResponse(
        items=[_to_document_response(d) for d in page], next_cursor=next_cursor
    )


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}",
    response_model=DocumentResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def get_document(
    workspace_id: UUID,
    document_id: UUID,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> DocumentResponse:
    require_capability(scope.caller_membership.role, READ_KNOWLEDGE_BASE)
    document = await scope.get_document.execute(
        GetDocumentCommand(workspace_id=workspace_id, document_id=document_id)
    )
    return _to_document_response(document)


@router.delete(
    "/workspaces/{workspace_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def delete_document(
    workspace_id: UUID,
    document_id: UUID,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> None:
    require_capability(scope.caller_membership.role, MANAGE_DOCUMENTS)
    await scope.delete_document.execute(
        DeleteDocumentCommand(workspace_id=workspace_id, document_id=document_id)
    )
