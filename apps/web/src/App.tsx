import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";

import { ChatPage } from "./routes/ChatPage";
import { DocumentsPage } from "./routes/DocumentsPage";
import { LoginPage } from "./routes/LoginPage";
import { useSessionBootstrap } from "./hooks/useAuth";
import { useAuthStore } from "./state/authStore";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Idempotent reads retry with jitter on transient failure (§5.4);
      // mutations never auto-retry beyond the idempotent design already
      // built into the chat-send flow itself.
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

function ProtectedRoute({ children }: { children: JSX.Element }): JSX.Element {
  const status = useAuthStore((s) => s.status);
  if (status === "loading") {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-neutral-500">Loading…</p>
      </main>
    );
  }
  if (status === "unauthenticated") {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function AppRoutes(): JSX.Element {
  useSessionBootstrap();
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* Path-explicit tenancy (§5.3, F-2) is the eventual route shape
          (w/{ws}/chat/{thread}); this sprint's single-default-workspace
          scope (useWorkspaceBootstrap) doesn't need it yet — see that
          hook's docstring for why a real workspace switcher waits on a
          GET /me/workspaces endpoint. */}
      <Route
        path="/chat"
        element={
          <ProtectedRoute>
            <ChatPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/documents"
        element={
          <ProtectedRoute>
            <DocumentsPage />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  );
}

export function App(): JSX.Element {
  return (
    <QueryClientProvider client={queryClient}>
      <AppRoutes />
    </QueryClientProvider>
  );
}
