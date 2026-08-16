import { apiFetch } from "../lib/apiClient";
import type { DocumentListResponse, DocumentRecord, InitiateUploadResponse } from "./types";

export function listDocuments(workspaceId: string, cursor?: string): Promise<DocumentListResponse> {
  const query = cursor !== undefined ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return apiFetch<DocumentListResponse>(`/v1/workspaces/${workspaceId}/documents${query}`);
}

export function getDocument(workspaceId: string, documentId: string): Promise<DocumentRecord> {
  return apiFetch<DocumentRecord>(`/v1/workspaces/${workspaceId}/documents/${documentId}`);
}

export function deleteDocument(workspaceId: string, documentId: string): Promise<void> {
  return apiFetch<void>(`/v1/workspaces/${workspaceId}/documents/${documentId}`, {
    method: "DELETE",
  });
}

function initiateUpload(
  workspaceId: string,
  body: { filename: string; mime: string; size_bytes: number; content_sha256: string },
): Promise<InitiateUploadResponse> {
  return apiFetch<InitiateUploadResponse>(`/v1/workspaces/${workspaceId}/documents:initiate`, {
    method: "POST",
    body,
  });
}

function confirmUpload(
  workspaceId: string,
  body: {
    document_id: string;
    object_key: string;
    filename: string;
    mime: string;
    size_bytes: number;
    content_sha256: string;
  },
): Promise<DocumentRecord> {
  return apiFetch<DocumentRecord>(`/v1/workspaces/${workspaceId}/documents:confirm`, {
    method: "POST",
    body,
  });
}

/** sha256 of the file's bytes, hex-encoded — computed client-side via
 * the Web Crypto API so :initiate can hand back a content-addressed
 * presigned URL before any bytes leave the browser (ADR-3.8). */
async function sha256Hex(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** Uploads directly to object storage via the presigned POST fields
 * from :initiate — never through apiFetch (this isn't a call to our
 * API, and the request body is multipart form data, not JSON). */
async function uploadToPresignedUrl(
  file: File,
  presigned: Pick<InitiateUploadResponse, "upload_url" | "upload_fields">,
): Promise<void> {
  const form = new FormData();
  for (const [key, value] of Object.entries(presigned.upload_fields)) {
    form.append(key, value);
  }
  form.append("file", file);
  const response = await fetch(presigned.upload_url, { method: "POST", body: form });
  if (!response.ok) {
    throw new Error(`upload failed: ${response.status} ${response.statusText}`);
  }
}

/** The full three-step flow (:initiate -> direct upload -> :confirm)
 * issue #44/#48 were built for, as one orchestrated call. */
export async function uploadDocument(workspaceId: string, file: File): Promise<DocumentRecord> {
  const contentSha256 = await sha256Hex(file);
  const initiated = await initiateUpload(workspaceId, {
    filename: file.name,
    mime: file.type || "application/octet-stream",
    size_bytes: file.size,
    content_sha256: contentSha256,
  });
  await uploadToPresignedUrl(file, initiated);
  return confirmUpload(workspaceId, {
    document_id: initiated.document_id,
    object_key: initiated.object_key,
    filename: file.name,
    mime: file.type || "application/octet-stream",
    size_bytes: file.size,
    content_sha256: contentSha256,
  });
}
