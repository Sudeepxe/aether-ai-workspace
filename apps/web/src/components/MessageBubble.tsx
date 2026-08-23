import type { Citation, MessageRole } from "../api/types";

function formatPageRange(citation: Citation): string | null {
  if (citation.page_start === null) {
    return null;
  }
  if (citation.page_end === null || citation.page_end === citation.page_start) {
    return `p. ${citation.page_start}`;
  }
  return `p. ${citation.page_start}–${citation.page_end}`;
}

/** One retrieved chunk's provenance (issue #59's snapshot fields,
 * ADR-8.6). A null chunk_id means the source was later deleted — the
 * snapshot text still renders (it survives independently, by design),
 * just visibly muted rather than as a dead link, per FR-KB-5. */
function CitationEntry({ citation }: { citation: Citation }): JSX.Element {
  const sourceRemoved = citation.chunk_id === null;
  const pageRange = formatPageRange(citation);
  return (
    <li
      className={sourceRemoved ? "text-neutral-400 italic" : "text-neutral-600"}
      title={sourceRemoved ? "This document has been deleted" : undefined}
    >
      {citation.document_title} — {citation.section_path}
      {pageRange !== null && <> ({pageRange})</>}
      {sourceRemoved && <> · source removed</>}
    </li>
  );
}

function CitationsFooter({ citations }: { citations: Citation[] }): JSX.Element | null {
  if (citations.length === 0) {
    return null;
  }
  return (
    <ul aria-label="Sources" className="mt-2 space-y-0.5 border-t border-neutral-200 pt-2 text-xs">
      {citations.map((citation) => (
        <CitationEntry key={citation.id} citation={citation} />
      ))}
    </ul>
  );
}

/**
 * Renders content as plain, whitespace-preserving text — never through
 * dangerouslySetInnerHTML. React's default child-text escaping is the
 * XSS-safety property here (§5.3: "model output renders through a
 * constrained pipeline"); a full markdown parser -> sanitizer ->
 * highlighter pipeline is deferred beyond this sprint — plain text is
 * not a security gap, it's the maximally safe default this sprint
 * doesn't need to move past yet. Retrieved chunk content surfaced via
 * `citations` is untrusted the same way (§2) — rendered as plain text
 * here too, never as markup or instructions.
 *
 * `grounded` is only meaningful for an assistant message (every
 * assistant turn since issue #60 is either a cited answer or a Gate-1
 * refusal — there's no third, ungrounded case anymore): `false` gets a
 * distinct visual treatment so a refusal reads as a deliberate "not in
 * the knowledge base" answer, not a generic or broken reply.
 */
export function MessageBubble({
  authorRole,
  content,
  pending = false,
  grounded = null,
  citations = [],
}: {
  authorRole: MessageRole;
  content: string;
  pending?: boolean;
  grounded?: boolean | null;
  citations?: Citation[];
}): JSX.Element {
  const isUser = authorRole === "user";
  const isRefusal = authorRole === "assistant" && grounded === false;
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[75%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap ${
          isUser
            ? "bg-neutral-900 text-white"
            : isRefusal
              ? "border border-dashed border-amber-300 bg-amber-50 text-amber-900"
              : "bg-neutral-100 text-neutral-900"
        }`}
      >
        {isRefusal && (
          <div className="mb-1 text-xs font-medium tracking-wide text-amber-700 uppercase">
            Not found in knowledge base
          </div>
        )}
        {content}
        {pending && (
          <span className="ml-1 animate-pulse" aria-hidden="true">
            ▍
          </span>
        )}
        {!isUser && <CitationsFooter citations={citations} />}
      </div>
    </div>
  );
}
