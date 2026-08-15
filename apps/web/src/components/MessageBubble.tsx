import type { MessageRole } from "../api/types";

/**
 * Renders content as plain, whitespace-preserving text — never through
 * dangerouslySetInnerHTML. React's default child-text escaping is the
 * XSS-safety property here (§5.3: "model output renders through a
 * constrained pipeline"); a full markdown parser -> sanitizer ->
 * highlighter pipeline is deferred to when grounded chat (S6) actually
 * produces markdown/citations worth rendering richly — plain text is
 * not a security gap, it's the maximally safe default this sprint
 * doesn't need to move past yet.
 */
export function MessageBubble({
  authorRole,
  content,
  pending = false,
}: {
  authorRole: MessageRole;
  content: string;
  pending?: boolean;
}): JSX.Element {
  const isUser = authorRole === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[75%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap ${
          isUser ? "bg-neutral-900 text-white" : "bg-neutral-100 text-neutral-900"
        }`}
      >
        {content}
        {pending && (
          <span className="ml-1 animate-pulse" aria-hidden="true">
            ▍
          </span>
        )}
      </div>
    </div>
  );
}
