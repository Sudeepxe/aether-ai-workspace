import { useCallback, useEffect } from "react";

import { getMe, login as loginRequest, logout as logoutRequest } from "../api/auth";
import { broadcastLogout, refreshAccessToken, setAccessToken } from "../lib/authRefresh";
import { useAuthStore } from "../state/authStore";

/** Runs once at app start: silently re-derives an access token from the
 * HttpOnly refresh cookie (ADR-7.1) rather than trusting anything
 * persisted client-side — a page reload always starts with no in-memory
 * token by design (lib/authRefresh.ts). */
export function useSessionBootstrap(): void {
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated);
  const setUnauthenticated = useAuthStore((s) => s.setUnauthenticated);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await refreshAccessToken();
        const user = await getMe();
        if (!cancelled) {
          setAuthenticated({ id: user.id, email: user.email, displayName: user.display_name });
        }
      } catch {
        if (!cancelled) {
          setUnauthenticated();
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs exactly once at app start by design
  }, []);
}

export function useLogin(): (email: string, password: string) => Promise<void> {
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated);
  return useCallback(
    async (email: string, password: string) => {
      const { access_token } = await loginRequest(email, password);
      setAccessToken(access_token);
      const user = await getMe();
      setAuthenticated({ id: user.id, email: user.email, displayName: user.display_name });
    },
    [setAuthenticated],
  );
}

export function useLogout(): () => Promise<void> {
  const setUnauthenticated = useAuthStore((s) => s.setUnauthenticated);
  return useCallback(async () => {
    try {
      await logoutRequest();
    } finally {
      broadcastLogout();
      setUnauthenticated();
    }
  }, [setUnauthenticated]);
}
