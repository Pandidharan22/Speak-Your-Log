import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { MESSAGES } from "../messages";
import { ConnectForm } from "./ConnectForm";

const m = MESSAGES.en;
const ok = { connected: true, last4: "ZZZZ" };

function setup(connect = vi.fn().mockResolvedValue(ok)) {
  const onConnected = vi.fn();
  render(<ConnectForm m={m} connect={connect} onConnected={onConnected} />);
  return { connect, onConnected, input: screen.getByLabelText(m.tokenLabel) as HTMLInputElement };
}

describe("ConnectForm", () => {
  it("masks the token and switches off every browser helper that could retain it", () => {
    const { input } = setup();
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(input).toHaveAttribute("spellcheck", "false");
    expect(input).toHaveAttribute("autocapitalize", "off");
  });

  it("is labelled and its help text is associated with the field", () => {
    const { input } = setup();
    expect(input).toHaveAccessibleName(m.tokenLabel);
    expect(input).toHaveAccessibleDescription(m.tokenHint);
  });

  it("sends the token once, then empties the field immediately", async () => {
    const { connect, onConnected, input } = setup();
    await userEvent.type(input, "tok-SECRET-1234");
    await userEvent.click(screen.getByRole("button", { name: m.connectButton }));
    expect(connect).toHaveBeenCalledTimes(1);
    expect(connect).toHaveBeenCalledWith("tok-SECRET-1234");
    expect(input.value).toBe("");
    await waitFor(() => expect(onConnected).toHaveBeenCalledWith(ok));
  });

  it("empties the field even when connecting fails, and shows a friendly message", async () => {
    const { input } = setup(vi.fn().mockRejectedValue(new ApiError(400, "token_rejected")));
    await userEvent.type(input, "tok-WRONG-0000");
    await userEvent.click(screen.getByRole("button", { name: m.connectButton }));
    expect(await screen.findByRole("alert")).toHaveTextContent(m.errors.token_rejected!);
    expect(input.value).toBe("");
    expect(document.body.textContent).not.toContain("tok-WRONG-0000");
  });

  it("does not submit an empty or whitespace-only token", async () => {
    const { connect, input } = setup();
    expect(screen.getByRole("button", { name: m.connectButton })).toBeDisabled();
    await userEvent.type(input, "   ");
    expect(screen.getByRole("button", { name: m.connectButton })).toBeDisabled();
    expect(connect).not.toHaveBeenCalled();
  });

  it("blocks a second submission while one is in flight", async () => {
    let release!: (v: typeof ok) => void;
    const connect = vi.fn().mockReturnValue(new Promise((r) => (release = r)));
    const { input } = setup(connect);
    await userEvent.type(input, "tok-SECRET-1234{Enter}");
    await userEvent.keyboard("{Enter}");
    expect(connect).toHaveBeenCalledTimes(1);
    release(ok);
  });

  it("maps unexpected errors to the generic message", async () => {
    const { input } = setup(vi.fn().mockRejectedValue(new Error("boom with details")));
    await userEvent.type(input, "tok-SECRET-1234{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(m.errors.unknown!);
    expect(document.body.textContent).not.toContain("boom with details");
  });

  it("links to Proof's token page safely", () => {
    setup();
    const link = screen.getByRole("link", { name: m.connectLink });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(link.getAttribute("rel")).toContain("noreferrer");
  });
});
