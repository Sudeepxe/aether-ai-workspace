import { apiFetch } from "../lib/apiClient";
import type { BudgetResponse, UsageResponse } from "./types";

export function getUsage(workspaceId: string): Promise<UsageResponse> {
  return apiFetch<UsageResponse>(`/v1/workspaces/${workspaceId}/usage`);
}

export function getBudget(workspaceId: string): Promise<BudgetResponse> {
  return apiFetch<BudgetResponse>(`/v1/workspaces/${workspaceId}/budget`);
}
