import { type FormEvent, useState } from "react";

import { useChatStreamStore } from "../state/chatStreamStore";

export function Composer({
  onSend,
  onCancel,
}: {
  onSend: (content: string) => Promise<void>;
  onCancel: () => void;
}): JSX.Element {
  const [draft, setDraft] = useState("");
  const activeStream = useChatStreamStore((s) => s.activeStream);
  const isStreaming =
    activeStream !== null &&
    (activeStream.phase === "submitted" || activeStream.phase === "streaming");

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const content = draft.trim();
    if (content.length === 0 || isStreaming) {
      return;
    }
    setDraft("");
    void onSend(content);
  };

  return (
    <form onSubmit={handleSubmit} className="flex items-end gap-2 border-t border-neutral-200 p-4">
      <label htmlFor="composer" className="sr-only">
        Message
      </label>
      <textarea
        id="composer"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            e.currentTarget.form?.requestSubmit();
          }
        }}
        rows={2}
        placeholder="Message Aether…"
        disabled={isStreaming}
        className="flex-1 resize-none rounded-md border border-neutral-300 px-3 py-2 text-sm focus:border-neutral-500 focus:outline-none disabled:bg-neutral-50"
      />
      {isStreaming ? (
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
        >
          Stop
        </button>
      ) : (
        <button
          type="submit"
          disabled={draft.trim().length === 0}
          className="rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-700 disabled:opacity-50"
        >
          Send
        </button>
      )}
    </form>
  );
}
