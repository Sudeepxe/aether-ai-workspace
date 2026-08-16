import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import type { DocumentRecord, DocumentStatus } from "../api/types";
import { useDeleteDocument, useDocuments, useUploadDocument } from "../hooks/useDocuments";
import { useWorkspaceBootstrap } from "../hooks/useWorkspaceBootstrap";

const STATUS_LABEL: Record<DocumentStatus, string> = {
  queued: "Queued",
  scanning: "Scanning",
  parsing: "Parsing",
  chunking: "Chunking",
  embedding: "Embedding",
  ready: "Ready",
  failed: "Failed",
};

const STATUS_STYLE: Record<DocumentStatus, string> = {
  queued: "border-neutral-200 text-neutral-500",
  scanning: "border-amber-300 bg-amber-50 text-amber-700",
  parsing: "border-amber-300 bg-amber-50 text-amber-700",
  chunking: "border-amber-300 bg-amber-50 text-amber-700",
  embedding: "border-amber-300 bg-amber-50 text-amber-700",
  ready: "border-green-300 bg-green-50 text-green-700",
  failed: "border-red-300 bg-red-50 text-red-700",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function StatusBadge({ document }: { document: DocumentRecord }): JSX.Element {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs ${STATUS_STYLE[document.status]}`}
      title={document.status === "failed" ? (document.failure_reason ?? undefined) : undefined}
    >
      {STATUS_LABEL[document.status]}
      {document.status === "failed" && document.failure_stage !== null
        ? ` (${document.failure_stage})`
        : ""}
    </span>
  );
}

export function DocumentsPage(): JSX.Element {
  const { ids, error } = useWorkspaceBootstrap(true);

  if (error !== null) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p role="alert" className="text-sm text-red-600">
          Couldn't set up your workspace: {error}
        </p>
      </main>
    );
  }
  if (ids === null) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-neutral-500">Setting up your workspace…</p>
      </main>
    );
  }
  return <DocumentsView workspaceId={ids.workspaceId} />;
}

export function DocumentsView({ workspaceId }: { workspaceId: string }): JSX.Element {
  const { data, isLoading, error: listError } = useDocuments(workspaceId);
  const upload = useUploadDocument(workspaceId);
  const deleteDoc = useDeleteDocument(workspaceId);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function handleFileChosen(files: FileList | null): void {
    const file = files?.[0];
    if (file !== undefined) {
      upload.mutate(file);
    }
    if (fileInputRef.current !== null) {
      fileInputRef.current.value = "";
    }
  }

  async function handleDelete(documentId: string): Promise<void> {
    setPendingDeleteId(documentId);
    try {
      await deleteDoc.mutateAsync(documentId);
    } finally {
      setPendingDeleteId(null);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-4 py-6">
      <header className="flex items-center justify-between border-b border-neutral-200 pb-3">
        <h1 className="text-sm font-semibold text-neutral-900">Documents</h1>
        <Link to="/chat" className="text-sm text-neutral-500 hover:text-neutral-900">
          Back to chat
        </Link>
      </header>

      <section className="flex items-center gap-3">
        <label
          htmlFor="document-upload"
          className="cursor-pointer rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
          aria-disabled={upload.isPending}
        >
          {upload.isPending ? "Uploading…" : "Upload document"}
        </label>
        <input
          id="document-upload"
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.md,.txt,.html"
          className="sr-only"
          disabled={upload.isPending}
          onChange={(e) => handleFileChosen(e.target.files)}
        />
        {upload.isError && (
          <p role="alert" className="text-sm text-red-600">
            Upload failed: {upload.error instanceof Error ? upload.error.message : "unknown error"}
          </p>
        )}
      </section>

      {isLoading && <p className="text-sm text-neutral-500">Loading documents…</p>}
      {listError && (
        <p role="alert" className="text-sm text-red-600">
          Couldn't load documents:{" "}
          {listError instanceof Error ? listError.message : "unknown error"}
        </p>
      )}
      {data !== undefined && data.items.length === 0 && (
        <p className="text-sm text-neutral-500">No documents yet — upload one to get started.</p>
      )}

      {data !== undefined && data.items.length > 0 && (
        <ul className="flex flex-col divide-y divide-neutral-200 rounded-md border border-neutral-200">
          {data.items.map((document) => (
            <li key={document.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="flex min-w-0 flex-col gap-1">
                <span className="truncate text-sm font-medium text-neutral-900">
                  {document.filename}
                </span>
                <span className="text-xs text-neutral-500">{formatBytes(document.size_bytes)}</span>
              </div>
              <div className="flex items-center gap-3">
                <StatusBadge document={document} />
                <button
                  type="button"
                  onClick={() => void handleDelete(document.id)}
                  disabled={pendingDeleteId === document.id}
                  className="text-sm text-neutral-500 hover:text-red-600 disabled:opacity-50"
                >
                  {pendingDeleteId === document.id ? "Deleting…" : "Delete"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
