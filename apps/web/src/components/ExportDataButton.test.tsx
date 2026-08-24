import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ExportJob } from "../api/types";
import { ExportDataButton } from "./ExportDataButton";

const { requestWorkspaceExport, getExportJob } = vi.hoisted(() => ({
  requestWorkspaceExport: vi.fn(),
  getExportJob: vi.fn(),
}));

vi.mock("../api/chat", () => ({ requestWorkspaceExport, getExportJob }));

function job(overrides: Partial<ExportJob>): ExportJob {
  return {
    id: "job-1",
    workspace_id: "ws-1",
    requested_by: "user-1",
    status: "queued",
    evidence: {},
    failure_reason: null,
    download_url: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

function renderButton(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ExportDataButton workspaceId="ws-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  requestWorkspaceExport.mockReset();
  getExportJob.mockReset();
});

describe("ExportDataButton", () => {
  it('shows "Preparing export…" immediately after requesting one', async () => {
    const user = userEvent.setup();
    requestWorkspaceExport.mockResolvedValue(job({ status: "queued" }));
    // Never resolves within the test — isolates this assertion from
    // refetchInterval's real 1s polling cadence, which a resolving mock
    // would race against.
    getExportJob.mockReturnValue(new Promise<ExportJob>(() => {}));
    renderButton();

    await user.click(screen.getByRole("button", { name: "Export data" }));

    expect(await screen.findByText("Preparing export…")).toBeInTheDocument();
  });

  it("shows a download link once the job is complete", async () => {
    const user = userEvent.setup();
    requestWorkspaceExport.mockResolvedValue(job({ status: "queued" }));
    getExportJob.mockResolvedValue(
      job({
        status: "complete",
        download_url: "https://storage.example/exports/ws-1/job-1.zip",
      }),
    );
    renderButton();

    await user.click(screen.getByRole("button", { name: "Export data" }));

    const link = await screen.findByRole("link", { name: "Download export" });
    expect(link).toHaveAttribute("href", "https://storage.example/exports/ws-1/job-1.zip");
  });

  it("shows a failure state without a download link", async () => {
    const user = userEvent.setup();
    requestWorkspaceExport.mockResolvedValue(job({ status: "queued" }));
    getExportJob.mockResolvedValue(job({ status: "failed", failure_reason: "boom" }));
    renderButton();

    await user.click(screen.getByRole("button", { name: "Export data" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Export failed");
    expect(screen.queryByRole("link", { name: "Download export" })).not.toBeInTheDocument();
  });
});
