import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type InterviewStatus, type Status } from "./api";
import { App } from "./App";
import type { Deps } from "./deps";
import { MESSAGES } from "./messages";
import type { VoiceHandlers } from "./voice";

const m = MESSAGES.en;
const START = { interview_id: "iv-1", livekit_url: "wss://x.livekit.cloud", token: "room-token" };

interface Fakes {
  deps: Deps;
  api: { [K in keyof Deps["api"]]: ReturnType<typeof vi.fn> };
  voice: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  handlers: () => VoiceHandlers;
}

function fakes(status: Status = { connected: true, last4: "ZZZZ" }): Fakes {
  const disconnect = vi.fn();
  let captured: VoiceHandlers | undefined;
  const voice = vi.fn(async (_info, handlers: VoiceHandlers) => {
    captured = handlers;
    return { disconnect };
  });
  const api = {
    session: vi.fn().mockResolvedValue(status),
    connectToken: vi.fn().mockResolvedValue({ connected: true, last4: "ABCD" }),
    disconnect: vi.fn().mockResolvedValue({ connected: false, last4: null }),
    startInterview: vi.fn().mockResolvedValue(START),
    interview: vi
      .fn()
      .mockResolvedValue({ state: "interviewing", preview: null, proof_url: null } satisfies InterviewStatus),
  };
  return {
    deps: { api, voice, pollMs: 10 } as unknown as Deps,
    api,
    voice,
    disconnect,
    handlers: () => captured!,
  };
}

beforeEach(() => localStorage.clear());

describe("boot", () => {
  it("shows the connect form to a device with no Proof token", async () => {
    const f = fakes({ connected: false, last4: null });
    render(<App deps={f.deps} />);
    expect(await screen.findByRole("heading", { name: m.connectTitle })).toBeInTheDocument();
  });

  it("shows the start screen, with only the last four characters, once a token is stored", async () => {
    render(<App deps={fakes().deps} />);
    expect(await screen.findByText(m.connectedAs("ZZZZ"))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: m.startButton })).toBeInTheDocument();
  });

  it("explains an unreachable server and lets the person retry", async () => {
    const f = fakes();
    f.api.session.mockRejectedValueOnce(new ApiError(0, "network_error"));
    render(<App deps={f.deps} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(m.errors.network_error!);
    expect(screen.getByText(m.waking)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: m.again }));
    expect(await screen.findByText(m.connectedAs("ZZZZ"))).toBeInTheDocument();
  });
});

describe("connecting and disconnecting", () => {
  it("moves on to the start screen after a successful connection", async () => {
    const f = fakes({ connected: false, last4: null });
    render(<App deps={f.deps} />);
    await userEvent.type(await screen.findByLabelText(m.tokenLabel), "tok-SECRET-1234{Enter}");
    expect(await screen.findByText(m.connectedAs("ABCD"))).toBeInTheDocument();
    expect(f.api.connectToken).toHaveBeenCalledWith("tok-SECRET-1234");
    expect(document.body.textContent).not.toContain("tok-SECRET-1234");
  });

  it("returns to the connect form after disconnecting", async () => {
    const f = fakes();
    render(<App deps={f.deps} />);
    await userEvent.click(await screen.findByRole("button", { name: m.disconnect }));
    expect(await screen.findByRole("heading", { name: m.connectTitle })).toBeInTheDocument();
    expect(f.api.disconnect).toHaveBeenCalledTimes(1);
  });
});

describe("starting an interview", () => {
  it("discloses before the first word that Google may use free-tier data", async () => {
    render(<App deps={fakes().deps} />);
    expect(await screen.findByText(m.privacyNotice)).toBeInTheDocument();
  });

  it("joins the room with the issued token and shows the waiting state", async () => {
    const f = fakes();
    render(<App deps={f.deps} />);
    await userEvent.click(await screen.findByRole("button", { name: m.startButton }));
    expect(await screen.findByText(m.statusWaiting)).toBeInTheDocument();
    expect(f.voice).toHaveBeenCalledWith(
      { url: START.livekit_url, token: START.token },
      expect.any(Object),
    );
  });

  it.each([
    ["busy_try_shortly", "busy_try_shortly"],
    ["voice_service_unavailable", "voice_service_unavailable"],
    ["too_many_interviews", "too_many_interviews"],
  ])("shows a friendly message for %s and stays on the start screen", async (code, key) => {
    const f = fakes();
    f.api.startInterview.mockRejectedValueOnce(new ApiError(503, code));
    render(<App deps={f.deps} />);
    await userEvent.click(await screen.findByRole("button", { name: m.startButton }));
    expect(await screen.findByRole("alert")).toHaveTextContent(m.errors[key]!);
    expect(f.voice).not.toHaveBeenCalled();
  });

  it("sends the person back to connect when the server says the token is missing", async () => {
    const f = fakes();
    f.api.startInterview.mockRejectedValueOnce(new ApiError(409, "token_not_connected"));
    render(<App deps={f.deps} />);
    await userEvent.click(await screen.findByRole("button", { name: m.startButton }));
    expect(await screen.findByRole("heading", { name: m.connectTitle })).toBeInTheDocument();
  });
});

describe("the interview", () => {
  async function startInterview(f: Fakes) {
    render(<App deps={f.deps} />);
    await userEvent.click(await screen.findByRole("button", { name: m.startButton }));
    // Wait for the interview screen itself (a finished interview shows a result, not the pill).
    await screen.findByRole("region", { name: m.appName });
    await waitFor(() => expect(f.voice).toHaveBeenCalled());
  }

  it("follows the voice: waiting, then listening, then the interviewer speaking", async () => {
    const f = fakes();
    await startInterview(f);
    act(() => f.handlers().onAgentJoined());
    expect(await screen.findByText(m.statusListening)).toBeInTheDocument();
    act(() => f.handlers().onAgentSpeaking(true));
    expect(await screen.findByText(m.statusAgentSpeaking)).toBeInTheDocument();
  });

  it("shows live captions, replacing interim text with the final text", async () => {
    const f = fakes();
    await startInterview(f);
    act(() => f.handlers().onLine({ id: "student:1", who: "student", text: "I tried", final: false }));
    act(() => f.handlers().onLine({ id: "student:1", who: "student", text: "I tried an IR sensor", final: true }));
    act(() => f.handlers().onLine({ id: "agent:1", who: "agent", text: "What broke?", final: true }));
    expect(await screen.findByText("I tried an IR sensor")).toBeInTheDocument();
    expect(screen.queryByText("I tried")).toBeNull();
    expect(screen.getByText("What broke?")).toBeInTheDocument();
  });

  it("explains a microphone refusal", async () => {
    const f = fakes();
    await startInterview(f);
    act(() => f.handlers().onError("mic_denied"));
    expect(await screen.findByRole("alert")).toHaveTextContent(m.micNeeded);
  });

  it("shows the exact read-back text and the public warning when the server reaches confirming", async () => {
    const f = fakes();
    f.api.interview.mockResolvedValue({
      state: "confirming",
      preview: { content: "I tried an IR sensor it kept being noisy", why: "cheaper ultrasonic was slow" },
      proof_url: null,
    });
    await startInterview(f);
    expect(await screen.findByTestId("preview-content")).toHaveTextContent("I tried an IR sensor it kept being noisy");
    expect(screen.getByTestId("preview-why")).toHaveTextContent("cheaper ultrasonic was slow");
    expect(screen.getByRole("note")).toHaveTextContent(/public/i);
  });

  it("shows the result link when posted, and stops polling and the microphone", async () => {
    const f = fakes();
    f.api.interview.mockResolvedValue({
      state: "posted",
      preview: { content: "c", why: "w" },
      proof_url: "https://proof.example.com/l/9",
    });
    await startInterview(f);
    const link = await screen.findByRole("link", { name: m.viewLog });
    expect(link).toHaveAttribute("href", "https://proof.example.com/l/9");
    expect(screen.queryByTestId("preview-content")).toBeNull(); // the read-back panel is gone
    const calls = f.api.interview.mock.calls.length;
    await new Promise((r) => setTimeout(r, 60));
    expect(f.api.interview.mock.calls.length).toBe(calls); // polling stopped
  });

  it("is honest when the outcome is unknown", async () => {
    const f = fakes();
    f.api.interview.mockResolvedValue({ state: "post_unknown", preview: null, proof_url: null });
    await startInterview(f);
    expect(await screen.findByRole("heading", { name: m.unknownTitle })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: m.viewLog })).toBeNull();
  });

  it("lets the person end the interview, leaves the room and reports that nothing was posted", async () => {
    const f = fakes();
    await startInterview(f);
    await userEvent.click(screen.getByRole("button", { name: m.endInterview }));
    expect(await screen.findByRole("heading", { name: m.cancelledTitle })).toBeInTheDocument();
    await waitFor(() => expect(f.disconnect).toHaveBeenCalled());
  });

  it("cannot be ended while the post is in flight", async () => {
    const f = fakes();
    f.api.interview.mockResolvedValue({
      state: "posting",
      preview: { content: "c", why: "w" },
      proof_url: null,
    });
    await startInterview(f);
    await screen.findByTestId("preview-content");
    expect(screen.getByRole("button", { name: m.endInterview })).toBeDisabled();
  });

  it("reports a lost session instead of polling forever", async () => {
    const f = fakes();
    f.api.interview.mockRejectedValue(new ApiError(401, "no_session"));
    await startInterview(f);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("leaves the room when the page is closed or the person navigates away", async () => {
    const f = fakes();
    const { unmount } = render(<App deps={f.deps} />);
    await userEvent.click(await screen.findByRole("button", { name: m.startButton }));
    await screen.findByText(m.statusWaiting);
    await waitFor(() => expect(f.voice).toHaveBeenCalledTimes(1));
    await act(async () => {}); // let the connection promise settle so there is a room to leave
    expect(f.disconnect).not.toHaveBeenCalled();
    unmount();
    expect(f.disconnect).toHaveBeenCalledTimes(1); // the microphone is released
  });
});

describe("language", () => {
  it("switches the whole interface to Tamil and back, and remembers the choice", async () => {
    render(<App deps={fakes().deps} />);
    await userEvent.click(await screen.findByRole("button", { name: MESSAGES.en.langSwitch }));
    expect(await screen.findByText(MESSAGES.ta.startTitle)).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("ta");
    expect(localStorage.getItem("syl.lang")).toBe("ta");
    await userEvent.click(screen.getByRole("button", { name: MESSAGES.ta.langSwitch }));
    expect(await screen.findByText(MESSAGES.en.startTitle)).toBeInTheDocument();
  });

  it("stores nothing but the language choice", async () => {
    render(<App deps={fakes().deps} />);
    await screen.findByText(m.connectedAs("ZZZZ"));
    expect(Object.keys(localStorage).filter((k) => k !== "syl.lang")).toEqual([]);
  });
});
