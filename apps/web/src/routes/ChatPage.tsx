import { Link } from "react-router-dom";

import { Composer } from "../components/Composer";
import { MessageList } from "../components/MessageList";
import { UsageIndicator } from "../components/UsageIndicator";
import { useLogout } from "../hooks/useAuth";
import { useSendMessage } from "../hooks/useSendMessage";
import { useWorkspaceBootstrap } from "../hooks/useWorkspaceBootstrap";
import { useAuthStore } from "../state/authStore";

export function ChatPage(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const logout = useLogout();
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

  return (
    <ChatThread
      workspaceId={ids.workspaceId}
      threadId={ids.threadId}
      userLabel={user?.displayName ?? ""}
      onLogout={() => void logout()}
    />
  );
}

function ChatThread({
  workspaceId,
  threadId,
  userLabel,
  onLogout,
}: {
  workspaceId: string;
  threadId: string;
  userLabel: string;
  onLogout: () => void;
}): JSX.Element {
  const { send, cancel } = useSendMessage(workspaceId, threadId);

  return (
    <main className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
        <h1 className="text-sm font-semibold text-neutral-900">Aether</h1>
        <div className="flex items-center gap-3 text-sm text-neutral-500">
          <Link to="/documents" className="hover:text-neutral-900">
            Documents
          </Link>
          <UsageIndicator workspaceId={workspaceId} />
          <span>{userLabel}</span>
          <button type="button" onClick={onLogout} className="hover:text-neutral-900">
            Sign out
          </button>
        </div>
      </header>
      <MessageList workspaceId={workspaceId} threadId={threadId} />
      <Composer onSend={send} onCancel={cancel} />
    </main>
  );
}
