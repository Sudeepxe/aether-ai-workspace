/** Reactive auth state for components (ADR-5.2: Zustand owns client
 * state, never server data — this is identity/session status, not a
 * cached server resource). The actual access token lives in
 * lib/authRefresh.ts, not here, so components re-render on auth status
 * changes without re-rendering on every token refresh. */
import { create } from "zustand";

export interface CurrentUser {
  id: string;
  email: string;
  displayName: string;
}

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthState {
  status: AuthStatus;
  user: CurrentUser | null;
  setAuthenticated: (user: CurrentUser) => void;
  setUnauthenticated: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  status: "loading",
  user: null,
  setAuthenticated: (user) => set({ status: "authenticated", user }),
  setUnauthenticated: () => set({ status: "unauthenticated", user: null }),
}));
