import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";

import { deleteDocument, listDocuments, uploadDocument } from "../api/documents";
import type { DocumentListResponse } from "../api/types";
import { TERMINAL_DOCUMENT_STATUSES } from "../api/types";

const DOCUMENTS_QUERY_KEY = (workspaceId: string) => ["documents", workspaceId];

/** Live status (FR-KB-2) via polling, matching 05-frontend.md's own
 * classification of documents as ordinary TanStack-Query-managed
 * server state (the same tier as usage), not the SSE streaming tier —
 * no push mechanism is specified for document status anywhere in the
 * architecture docs. Polls only while at least one document is still
 * mid-pipeline; stops once everything has reached a terminal state, so
 * an idle document list doesn't poll forever. */
export function useDocuments(workspaceId: string): UseQueryResult<DocumentListResponse> {
  return useQuery({
    queryKey: DOCUMENTS_QUERY_KEY(workspaceId),
    queryFn: () => listDocuments(workspaceId),
    enabled: workspaceId.length > 0,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      const stillProcessing = items.some((d) => !TERMINAL_DOCUMENT_STATUSES.has(d.status));
      return stillProcessing ? 2000 : false;
    },
  });
}

export function useUploadDocument(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadDocument(workspaceId, file),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_QUERY_KEY(workspaceId) });
    },
  });
}

export function useDeleteDocument(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => deleteDocument(workspaceId, documentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_QUERY_KEY(workspaceId) });
    },
  });
}
