/**
 * Tenant data export (issue #85, FR-AD-5): request -> poll -> download.
 * Polling stops once the job leaves queued/running, matching
 * useDocuments.ts's status-polling precedent.
 */
import {
  useMutation,
  useQuery,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { getExportJob, requestWorkspaceExport } from "../api/chat";
import type { ExportJob } from "../api/types";

export function useRequestExport(workspaceId: string): UseMutationResult<ExportJob, Error, void> {
  return useMutation({
    mutationFn: () => requestWorkspaceExport(workspaceId),
  });
}

export function useExportJob(workspaceId: string, jobId: string | null): UseQueryResult<ExportJob> {
  return useQuery({
    queryKey: ["export-job", workspaceId, jobId],
    queryFn: () => getExportJob(workspaceId, jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1000 : false;
    },
  });
}
