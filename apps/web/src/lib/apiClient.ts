/**
 * Thin fetch wrapper (§4.2): JSON in/out, RFC 9457 Problem+JSON errors
 * surfaced as a typed ApiError, Bearer auth from the in-memory access
 * token (never localStorage — Ch.5's "common mistakes": localStorage
 * tokens are XSS-stealable). A 401 triggers exactly one silent refresh
 * attempt via the cross-tab-coordinated refreshAccessToken() before
 * failing for real.
 */
import { getAccessToken, refreshAccessToken } from "./authRefresh";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly correlationId: string | undefined;

  constructor(
    status: number,
    message: string,
    options?: { code?: string; correlationId?: string },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = options?.code;
    this.correlationId = options?.correlationId;
  }
}

interface ProblemJson {
  title?: string;
  detail?: string;
  code?: string;
  correlation_id?: string;
}

export async function toApiError(response: Response): Promise<ApiError> {
  let problem: ProblemJson = {};
  try {
    problem = (await response.clone().json()) as ProblemJson;
  } catch {
    // non-JSON error body — fall through with a generic message
  }
  const message = problem.detail ?? problem.title ?? response.statusText;
  return new ApiError(response.status, message, {
    code: problem.code,
    correlationId: problem.correlation_id,
  });
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Set for a request that must not trigger the 401-retry-with-refresh
   * dance — used by the refresh call itself, to avoid infinite recursion. */
  skipAuthRetry?: boolean;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const token = getAccessToken();
    const headers: Record<string, string> = {
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token !== null ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    };
    return fetch(path, {
      method: options.method ?? "GET",
      headers,
      credentials: "include",
      signal: options.signal,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  };

  let response = await doFetch();
  if (response.status === 401 && !options.skipAuthRetry) {
    try {
      await refreshAccessToken();
    } catch {
      throw await toApiError(response);
    }
    response = await doFetch();
  }

  if (!response.ok) {
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** For the one caller (the SSE POST) that needs the raw Response rather
 * than a parsed body — streaming responses can't go through apiFetch's
 * JSON parsing. */
export async function apiFetchRaw(path: string, options: RequestOptions = {}): Promise<Response> {
  const token = getAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token !== null ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  };
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers,
    credentials: "include",
    signal: options.signal,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  if (response.status === 401 && !options.skipAuthRetry) {
    await refreshAccessToken();
    return apiFetchRaw(path, { ...options, skipAuthRetry: true });
  }
  return response;
}
