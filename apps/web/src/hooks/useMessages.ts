import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { listMessages, submitFeedback } from "../api/chat";
import type { Feedback, FeedbackRating, MessageListResponse } from "../api/types";

const MESSAGES_QUERY_KEY = (workspaceId: string, threadId: string) => [
  "messages",
  workspaceId,
  threadId,
];

export function useMessages(
  workspaceId: string,
  threadId: string,
): UseQueryResult<MessageListResponse> {
  return useQuery({
    queryKey: MESSAGES_QUERY_KEY(workspaceId, threadId),
    queryFn: () => listMessages(workspaceId, threadId),
    enabled: workspaceId.length > 0 && threadId.length > 0,
  });
}

export function useSubmitFeedback(
  workspaceId: string,
  threadId: string,
): UseMutationResult<Feedback, Error, { messageId: string; rating: FeedbackRating }> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ messageId, rating }: { messageId: string; rating: FeedbackRating }) =>
      submitFeedback(workspaceId, threadId, messageId, { rating }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: MESSAGES_QUERY_KEY(workspaceId, threadId) });
    },
  });
}
