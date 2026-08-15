/**
 * In-memory access token + cross-tab-coordinated refresh (ADR-7.1/7.2,
 * Ch.5 self-review F-1).
 *
 * The token lives in a module-level variable, never localStorage/
 * sessionStorage (XSS-stealable — Ch.5's "common mistakes"). It is lost
 * on reload by design; ``bootstrapSession()`` (called once at app start)
 * re-derives it from the HttpOnly refresh cookie via POST /auth/refresh.
 *
 * F-1 (the real catch in Ch.5's self-review): N tabs can detect expiry
 * simultaneously and race the refresh endpoint. With rotation + reuse
 * detection (ADR-7.2), the losing tabs' reuse of an already-rotated
 * refresh token would trip family revocation and log every tab out — a
 * bug that only manifests with two+ tabs open, one of the hardest
 * classes to reproduce. Fixed with two cooperating mechanisms:
 * - Web Locks (``navigator.locks``): only one tab's refresh call is
 *   ever in flight at a time, cooperatively serialized across tabs.
 * - BroadcastChannel: the tab that actually refreshed broadcasts the
 *   new token, so a tab that was waiting on the lock can pick up the
 *   already-fresh token instead of making a second, redundant (and, per
 *   F-1, dangerous) refresh call.
 */

const REFRESH_LOCK_NAME = "aether-auth-refresh";
const BROADCAST_CHANNEL_NAME = "aether-auth";

let accessToken: string | null = null;
let lastRefreshedAt = 0;

const channel: BroadcastChannel | null =
  typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(BROADCAST_CHANNEL_NAME) : null;

channel?.addEventListener("message", (event: MessageEvent<unknown>) => {
  const data = event.data as { type?: string; accessToken?: string } | undefined;
  if (data?.type === "token-refreshed" && typeof data.accessToken === "string") {
    accessToken = data.accessToken;
    lastRefreshedAt = Date.now();
  }
  if (data?.type === "logged-out") {
    accessToken = null;
  }
});

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  lastRefreshedAt = Date.now();
}

export function broadcastLogout(): void {
  accessToken = null;
  channel?.postMessage({ type: "logged-out" });
}

export class AuthRefreshError extends Error {}

/** A refresh that happened (in this tab or another) within this window
 * satisfies a concurrent caller without it making its own network call —
 * the actual mechanism that prevents the F-1 race, not just the lock. */
const RECENT_REFRESH_WINDOW_MS = 2000;

export async function refreshAccessToken(): Promise<string> {
  const runRefresh = async (): Promise<string> => {
    if (Date.now() - lastRefreshedAt < RECENT_REFRESH_WINDOW_MS && accessToken !== null) {
      return accessToken;
    }
    const response = await fetch("/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
    });
    if (!response.ok) {
      throw new AuthRefreshError("refresh failed");
    }
    const body = (await response.json()) as { access_token: string };
    setAccessToken(body.access_token);
    channel?.postMessage({ type: "token-refreshed", accessToken: body.access_token });
    return body.access_token;
  };

  if (typeof navigator !== "undefined" && "locks" in navigator) {
    return navigator.locks.request(REFRESH_LOCK_NAME, runRefresh);
  }
  return runRefresh();
}
