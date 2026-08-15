import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStreamStore } from "../state/chatStreamStore";
import { Composer } from "./Composer";

beforeEach(() => {
  useChatStreamStore.getState().clear();
});

describe("Composer", () => {
  it("submits the trimmed draft on Enter and clears the field", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<Composer onSend={onSend} onCancel={vi.fn()} />);

    const textarea = screen.getByLabelText("Message");
    await user.type(textarea, "  hello there  ");
    await user.keyboard("{Enter}");

    expect(onSend).toHaveBeenCalledWith("hello there");
    expect(textarea).toHaveValue("");
  });

  it("does not submit on Shift+Enter — inserts a newline instead", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<Composer onSend={onSend} onCancel={vi.fn()} />);

    await user.type(screen.getByLabelText("Message"), "line one{Shift>}{Enter}{/Shift}line two");

    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not submit an empty or whitespace-only draft", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<Composer onSend={onSend} onCancel={vi.fn()} />);

    await user.type(screen.getByLabelText("Message"), "   ");
    await user.keyboard("{Enter}");

    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows Stop instead of Send while a stream is active, and wires it to onCancel", async () => {
    useChatStreamStore.getState().begin("thread-1", "gen-1");
    useChatStreamStore.getState().setPhase("streaming");
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<Composer onSend={vi.fn()} onCancel={onCancel} />);

    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    const stopButton = screen.getByRole("button", { name: "Stop" });
    await user.click(stopButton);

    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("disables the textarea while a stream is active", () => {
    useChatStreamStore.getState().begin("thread-1", "gen-1");
    useChatStreamStore.getState().setPhase("submitted");
    render(<Composer onSend={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByLabelText("Message")).toBeDisabled();
  });
});
