import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { getBudget } from "../api/metering";
import type { BudgetResponse } from "../api/types";

export function useBudget(workspaceId: string): UseQueryResult<BudgetResponse> {
  return useQuery({
    queryKey: ["budget", workspaceId],
    queryFn: () => getBudget(workspaceId),
    enabled: workspaceId.length > 0,
    // No websocket/SSE push for budget changes — a chat turn's settle
    // path also invalidates this key (useSendMessage.ts) so it's fresh
    // right after sending; this refetch interval is the fallback for
    // spend from other tabs/members or a soft-limit crossed elsewhere.
    refetchInterval: 30_000,
  });
}
