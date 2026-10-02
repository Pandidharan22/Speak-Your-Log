import { useEffect, useRef, useState } from "react";
import { ApiError, TERMINAL_STATES, type InterviewStart, type InterviewState, type InterviewStatus } from "../api";
import { errorText, type Messages } from "../messages";
import { upsertLine, type Line } from "../transcript";
import type { Deps } from "../deps";
import { ReadBack } from "./ReadBack";
import { Result } from "./Result";
import { Transcript } from "./Transcript";

interface Props {
  m: Messages;
  info: InterviewStart;
  deps: Deps;
  onAgain: () => void;
}

type Finished = Extract<InterviewState, "posted" | "post_unknown" | "failed" | "cancelled">;

const MAX_POLL_FAILURES = 6;
const DISCONNECT_AFTER_FINISH_MS = 8000; // lets the interviewer finish saying goodbye

export function Interview({ m, info, deps, onAgain }: Props) {
  const pollMs = deps.pollMs ?? 1500;
  const [lines, setLines] = useState<Line[]>([]);
  const [agentJoined, setAgentJoined] = useState(false);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [status, setStatus] = useState<InterviewStatus | null>(null);
  const [endedByUser, setEndedByUser] = useState(false);
  const connection = useRef<{ disconnect(): void } | null>(null);

  const finished: Finished | null =
    status && TERMINAL_STATES.has(status.state)
      ? (status.state as Finished)
      : endedByUser
        ? "cancelled"
        : null;

  // ---- the voice call -----------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    deps
      .voice(
        { url: info.livekit_url, token: info.token },
        {
          onLine: (line) => setLines((current) => upsertLine(current, line)),
          onAgentSpeaking: setAgentSpeaking,
          onAgentJoined: () => setAgentJoined(true),
          onDisconnected: () => setAgentSpeaking(false),
          onError: (code) => setProblem(code === "mic_denied" ? m.micNeeded : errorText(m, "voice_service_unavailable")),
        },
      )
      .then((c) => {
        if (cancelled) c.disconnect();
        else connection.current = c;
      })
      .catch(() => undefined); // already reported through onError
    return () => {
      cancelled = true;
      connection.current?.disconnect();
      connection.current = null;
    };
    // The room is joined once per interview; `m` only supplies message text.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deps, info]);

  // ---- progress from the server (state, read-back text, result link) ------------------------
  useEffect(() => {
    if (finished) return;
    let failures = 0;
    let stopped = false;
    const tick = async () => {
      try {
        const next = await deps.api.interview(info.interview_id);
        if (!stopped) {
          failures = 0;
          setStatus(next);
        }
      } catch (e) {
        failures += 1;
        if (!stopped && (failures >= MAX_POLL_FAILURES || (e instanceof ApiError && e.status === 401))) {
          setProblem(errorText(m, e instanceof ApiError ? e.code : "network_error"));
        }
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), pollMs);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deps, info.interview_id, pollMs, finished]);

  // Once finished, stop sending the microphone (after the goodbye has had time to play).
  useEffect(() => {
    if (!finished) return;
    const timer = setTimeout(() => connection.current?.disconnect(), endedByUser ? 0 : DISCONNECT_AFTER_FINISH_MS);
    return () => clearTimeout(timer);
  }, [finished, endedByUser]);

  const statusText = !agentJoined
    ? m.statusWaiting
    : agentSpeaking
      ? m.statusAgentSpeaking
      : m.statusListening;
  const showReadBack =
    !finished && status?.preview && (status.state === "confirming" || status.state === "posting");

  return (
    <section aria-labelledby="interview-title" className="interview">
      <h2 id="interview-title">{m.appName}</h2>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      {finished ? (
        <Result m={m} state={finished} proofUrl={status?.proof_url ?? null} onAgain={onAgain} />
      ) : (
        <>
          <p role="status" className={`pill ${agentSpeaking ? "speaking" : "listening"}`}>
            <span className="dot" aria-hidden="true" />
            {statusText}
          </p>
          {showReadBack && status?.preview && <ReadBack m={m} preview={status.preview} />}
          <button
            type="button"
            onClick={() => setEndedByUser(true)}
            disabled={status?.state === "posting"}
          >
            {m.endInterview}
          </button>
        </>
      )}
      <Transcript m={m} lines={lines} />
    </section>
  );
}
