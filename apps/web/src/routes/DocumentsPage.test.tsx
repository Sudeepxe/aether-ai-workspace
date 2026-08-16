import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentListResponse, DocumentRecord } from "../api/types";
import { DocumentsView } from "./DocumentsPage";

const { listDocuments, uploadDocument, deleteDocument } = vi.hoisted(() => ({
  listDocuments: vi.fn(),
  uploadDocument: vi.fn(),
  deleteDocument: vi.fn(),
}));

vi.mock("../api/documents", () => ({ listDocuments, uploadDocument, deleteDocument }));

function renderWithProviders(workspaceId: string): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DocumentsView workspaceId={workspaceId} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function doc(overrides: Partial<DocumentRecord>): DocumentRecord {
  return {
    id: "doc-1",
    workspace_id: "ws-1",
    filename: "notes.md",
    mime: "text/markdown",
    size_bytes: 2048,
    status: "ready",
    failure_stage: null,
    failure_reason: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  listDocuments.mockReset();
  uploadDocument.mockReset();
  deleteDocument.mockReset();
});

describe("DocumentsView", () => {
  it("shows an empty state when there are no documents", async () => {
    listDocuments.mockResolvedValue({
      items: [],
      next_cursor: null,
    } satisfies DocumentListResponse);
    renderWithProviders("ws-1");

    expect(
      await screen.findByText("No documents yet — upload one to get started."),
    ).toBeInTheDocument();
  });

  it("lists documents with their status badge", async () => {
    listDocuments.mockResolvedValue({
      items: [doc({ filename: "report.pdf", status: "ready" })],
      next_cursor: null,
    } satisfies DocumentListResponse);
    renderWithProviders("ws-1");

    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("shows the failure stage on a failed document", async () => {
    listDocuments.mockResolvedValue({
      items: [
        doc({
          filename: "bad.pdf",
          status: "failed",
          failure_stage: "scanning",
          failure_reason: "malware detected",
        }),
      ],
      next_cursor: null,
    } satisfies DocumentListResponse);
    renderWithProviders("ws-1");

    expect(await screen.findByText("Failed (scanning)")).toBeInTheDocument();
  });

  it("uploads a chosen file and refreshes the list", async () => {
    listDocuments
      .mockResolvedValueOnce({ items: [], next_cursor: null } satisfies DocumentListResponse)
      .mockResolvedValueOnce({
        items: [doc({ filename: "new.md" })],
        next_cursor: null,
      } satisfies DocumentListResponse);
    uploadDocument.mockResolvedValue(doc({ filename: "new.md" }));
    const user = userEvent.setup();
    renderWithProviders("ws-1");
    await screen.findByText("No documents yet — upload one to get started.");

    const file = new File(["# hello"], "new.md", { type: "text/markdown" });
    const input = document.getElementById("document-upload") as HTMLInputElement;
    await user.upload(input, file);

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledWith("ws-1", file));
    expect(await screen.findByText("new.md")).toBeInTheDocument();
  });

  it("deletes a document when Delete is clicked", async () => {
    listDocuments
      .mockResolvedValueOnce({
        items: [doc({ id: "doc-1", filename: "gone.md" })],
        next_cursor: null,
      } satisfies DocumentListResponse)
      .mockResolvedValueOnce({ items: [], next_cursor: null } satisfies DocumentListResponse);
    deleteDocument.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithProviders("ws-1");
    await screen.findByText("gone.md");

    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteDocument).toHaveBeenCalledWith("ws-1", "doc-1"));
    await waitFor(() =>
      expect(screen.getByText("No documents yet — upload one to get started.")).toBeInTheDocument(),
    );
  });
});
