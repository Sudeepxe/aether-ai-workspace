import { useMessages } from "../hooks/useMessages";
import { useChatStreamStore } from "../state/chatStreamStore";
import { MessageBubble } from "./MessageBubble";

/**
 * Message list virtualization (TanStack Virtual) is called for in the
 * design (§5.3) once threads grow unboundedly — deliberately not built
 * for this sprint's echo-proof scope (a handful of short-lived test
 * messages, not a long real conversation history); tracked as a known
 * gap for whichever sprint first ships a thread long enough to need it.
 */
export function MessageList({
  workspaceId,
  threadId,
}: {
  workspaceId: string;
  threadId: string;
}): JSX.Element {
  const { data, isLoading, isError } = useMessages(workspaceId, threadId);
  const activeStream = useChatStreamStore((s) => s.activeStream);

  if (isLoading) {
    return <p className="text-sm text-neutral-500">Loading…</p>;
  }
  if (isError) {
    return <p className="text-sm text-red-600">Couldn't load this conversation.</p>;
  }

  const messages = data?.items ?? [];
  // Oldest first for display — the API returns newest-first (its
  // natural pagination order, ADR-8.2).
  const ordered = [...messages].reverse();

  return (
    <div
      className="flex flex-1 flex-col gap-3 overflow-y-auto p-4"
      aria-live="polite"
      aria-label="Conversation"
    >
      {ordered.length === 0 && activeStream === null && (
        <p className="text-sm text-neutral-400">Say hello to start the conversation.</p>
      )}
      {ordered.map((message) => (
        <MessageBubble
          key={message.id}
          authorRole={message.role}
          content={message.content}
          grounded={message.grounded}
          citations={message.citations}
        />
      ))}
      {activeStream !== null && activeStream.threadId === threadId && (
        <MessageBubble
          authorRole="assistant"
          content={activeStream.content}
          pending={activeStream.phase === "streaming" || activeStream.phase === "submitted"}
          grounded={activeStream.grounded}
          citations={activeStream.citations}
        />
      )}
      {activeStream?.phase === "errored" && (
        <p role="alert" className="text-sm text-red-600">
          {activeStream.errorMessage ?? "The message failed to send."}
        </p>
      )}
    </div>
  );
}
