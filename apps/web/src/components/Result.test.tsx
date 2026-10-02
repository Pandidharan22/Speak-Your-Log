import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MESSAGES } from "../messages";
import { ReadBack } from "./ReadBack";
import { Result, safeProofUrl } from "./Result";

const m = MESSAGES.en;

describe("safeProofUrl", () => {
  it.each([
    ["https://proof.example.com/u/ana/logs/1", "https://proof.example.com/u/ana/logs/1"],
    [null, null],
    ["", null],
    ["javascript:alert(1)", null],
    ["data:text/html,<script>1</script>", null],
    ["http://insecure.example/x", null],
    ["not a url", null],
    ["//evil.example/x", null],
  ])("%s -> %s", (input, expected) => expect(safeProofUrl(input)).toBe(expected));
});

describe("Result", () => {
  it("links to the posted log, opening safely in a new tab", () => {
    render(<Result m={m} state="posted" proofUrl="https://proof.example.com/l/1" onAgain={() => {}} />);
    const link = screen.getByRole("link", { name: m.viewLog });
    expect(link).toHaveAttribute("href", "https://proof.example.com/l/1");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("never renders an unsafe URL as a link, but still says it was posted", () => {
    render(<Result m={m} state="posted" proofUrl="javascript:alert(1)" onAgain={() => {}} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(m.postedNoLink)).toBeInTheDocument();
  });

  it.each([
    ["post_unknown", MESSAGES.en.unknownTitle, /check your Proof profile/i],
    ["failed", MESSAGES.en.failedTitle, /nothing was posted/i],
    ["cancelled", MESSAGES.en.cancelledTitle, /nothing was posted/i],
  ] as const)("%s tells the truth about what happened", (state, title, body) => {
    render(<Result m={m} state={state} proofUrl={null} onAgain={() => {}} />);
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.getByText(body)).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("moves keyboard focus to the outcome heading and offers to start again", async () => {
    const again = vi.fn();
    render(<Result m={m} state="cancelled" proofUrl={null} onAgain={again} />);
    expect(screen.getByRole("heading", { name: m.cancelledTitle })).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: m.again }));
    expect(again).toHaveBeenCalled();
  });
});

describe("ReadBack", () => {
  it("shows the exact text, the public warning and what to say", () => {
    render(<ReadBack m={m} preview={{ content: "I tried an IR sensor it was noisy", why: "cheaper  and fast" }} />);
    expect(screen.getByTestId("preview-content")).toHaveTextContent("I tried an IR sensor it was noisy");
    expect(screen.getByTestId("preview-why").textContent).toBe("cheaper  and fast"); // spacing untouched
    expect(screen.getByRole("note")).toHaveTextContent(/public/i);
    expect(screen.getByText(m.sayYes)).toBeInTheDocument();
  });

  it("renders the student's words as plain text, never as HTML", () => {
    const { container } = render(
      <ReadBack m={m} preview={{ content: "<img src=x onerror=alert(1)> hello", why: "<b>bold</b>" }} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByTestId("preview-content").textContent).toContain("<img src=x");
  });
});
