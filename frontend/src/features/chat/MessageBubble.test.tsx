import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Message } from "../../lib/types";
import { MessageBubble } from "./MessageBubble";
import type { ChatItem } from "./useConversation";

function message(overrides: Partial<Message>): Message {
  return {
    id: "m1",
    role: "ASSISTANT",
    content: "",
    created_at: new Date().toISOString(),
    answer_status: null,
    response_kind: null,
    confidence: null,
    citations: [],
    handoff_reason: null,
    author_name: null,
    feedback: null,
    ...overrides,
  };
}

function renderItem(item: ChatItem, handlers: Partial<{ onRetry: () => void; onRequestHuman: () => void }> = {}) {
  return render(
    <MessageBubble
      item={item}
      canRequestHuman
      onRetry={handlers.onRetry ?? vi.fn()}
      onRequestHuman={handlers.onRequestHuman ?? vi.fn()}
      onRate={vi.fn().mockResolvedValue(undefined)}
    />,
  );
}

describe("MessageBubble", () => {
  it("shows a grounded answer with expandable sources", async () => {
    renderItem({
      key: "m1",
      message: message({
        content: "Standard shipping takes 3-5 business days.",
        answer_status: "ANSWERED",
        response_kind: "RAG_ANSWER",
        confidence: "HIGH",
        citations: [{
          evidence_id: "S1", chunk_id: "c1", document_id: "d1", document_version_id: "v1", document_title: "Shipping Policy",
          version: 1, source_type: "PDF", source_uri: null, authority: "OFFICIAL_POLICY", section_title: "Standard Shipping",
          heading_path: null, page_number: 2, chunk_index: 1, effective_date: "2026-01-01",
          excerpt: "Standard shipping takes 3-5 business days after your order ships.",
        }],
      }),
    });
    expect(screen.getByText("Standard shipping takes 3-5 business days.")).toBeInTheDocument();
    const source = screen.getByRole("button", { name: /Shipping Policy/ });
    expect(screen.getByText("Page 2 · Standard Shipping")).toBeInTheDocument();
    expect(source).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(source);
    expect(source).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/after your order ships/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Helpful" })).toBeInTheDocument();
  });

  it("makes abstention explicit and offers a human", async () => {
    const onRequestHuman = vi.fn();
    renderItem(
      { key: "m2", message: message({ content: "I couldn't find enough information.", answer_status: "ABSTAINED", response_kind: "ABSTENTION" }) },
      { onRequestHuman },
    );
    expect(screen.getByText("Not found in our documentation")).toBeInTheDocument();
    expect(screen.queryByText("Sources")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Talk to a human/ }));
    expect(onRequestHuman).toHaveBeenCalledOnce();
  });

  it("offers retry for a message that failed to send", async () => {
    const onRetry = vi.fn();
    renderItem(
      { key: "local-1", message: message({ id: "", role: "USER", content: "Hello?" }), local: { status: "failed", clientId: "x", error: "We can't reach the support service." } },
      { onRetry },
    );
    expect(screen.getByRole("alert")).toHaveTextContent("We can't reach the support service.");
    await userEvent.click(screen.getByRole("button", { name: /Retry/ }));
    expect(onRetry).toHaveBeenCalledWith("local-1");
  });

  it("labels human agent replies", () => {
    renderItem({ key: "m3", message: message({ role: "HUMAN_AGENT", content: "I'm looking into it.", author_name: "Sam (Support)" }) });
    expect(screen.getByText("Sam (Support)")).toBeInTheDocument();
  });
});
