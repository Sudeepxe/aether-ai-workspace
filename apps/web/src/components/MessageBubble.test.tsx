import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Citation } from "../api/types";
import { MessageBubble } from "./MessageBubble";

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    id: "cit-1",
    chunk_id: "chunk-1",
    document_title: "pricing.md",
    section_path: "Pricing",
    page_start: 1,
    page_end: 1,
    ...overrides,
  };
}

describe("MessageBubble", () => {
  it("renders a grounded message's citation list with correct provenance text", () => {
    render(
      <MessageBubble
        authorRole="assistant"
        content="Acme costs $10/mo."
        grounded={true}
        citations={[
          citation({ id: "cit-1", document_title: "pricing.md", section_path: "Pricing" }),
          citation({
            id: "cit-2",
            document_title: "handbook.md",
            section_path: "Overview",
            page_start: 3,
            page_end: 4,
          }),
        ]}
      />,
    );

    const sources = screen.getByRole("list", { name: "Sources" });
    expect(sources).toHaveTextContent("pricing.md — Pricing (p. 1)");
    expect(sources).toHaveTextContent("handbook.md — Overview (p. 3–4)");
  });

  it("gives a refusal message (grounded=false) a distinct visual treatment", () => {
    render(
      <MessageBubble
        authorRole="assistant"
        content="I don't have information about that in the knowledge base."
        grounded={false}
        citations={[]}
      />,
    );

    expect(screen.getByText("Not found in knowledge base")).toBeInTheDocument();
    // No citation list at all for a refusal — nothing was retrieved.
    expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
  });

  it("does not apply the refusal treatment to an ordinary grounded answer", () => {
    render(
      <MessageBubble
        authorRole="assistant"
        content="Acme costs $10/mo."
        grounded={true}
        citations={[citation()]}
      />,
    );

    expect(screen.queryByText("Not found in knowledge base")).not.toBeInTheDocument();
  });

  it('renders a "source removed" citation (null chunk_id) muted, without a dead link', () => {
    render(
      <MessageBubble
        authorRole="assistant"
        content="Acme costs $10/mo."
        grounded={true}
        citations={[citation({ chunk_id: null })]}
      />,
    );

    const sources = screen.getByRole("list", { name: "Sources" });
    expect(sources).toHaveTextContent("source removed");
    // Still renders the snapshot provenance text, not a blank/broken entry.
    expect(sources).toHaveTextContent("pricing.md — Pricing");
    expect(sources.querySelector("a")).toBeNull();
  });

  it("never shows a refusal treatment or citations footer for a user message", () => {
    render(
      <MessageBubble authorRole="user" content="hello" grounded={false} citations={[citation()]} />,
    );

    expect(screen.queryByText("Not found in knowledge base")).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
  });
});
