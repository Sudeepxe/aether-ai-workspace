import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { listMessages } from "../api/chat";
import type { MessageListResponse } from "../api/types";

export function useMessages(
  workspaceId: string,
  threadId: string,
): UseQueryResult<MessageListResponse> {
  return useQuery({
    queryKey: ["messages", workspaceId, threadId],
    queryFn: () => listMessages(workspaceId, threadId),
    enabled: workspaceId.length > 0 && threadId.length > 0,
  });
}
