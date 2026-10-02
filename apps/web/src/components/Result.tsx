import type { InterviewState } from "../api";
import type { Messages } from "../messages";

interface Props {
  m: Messages;
  state: Extract<InterviewState, "posted" | "post_unknown" | "failed" | "cancelled">;
  proofUrl: string | null;
  onAgain: () => void;
}

// Only an https link on the Proof site is ever shown as a link, whatever the server sent.
export function safeProofUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

export function Result({ m, state, proofUrl, onAgain }: Props) {
  const link = safeProofUrl(proofUrl);
  const copy = {
    posted: { title: m.postedTitle, body: m.postedBody },
    post_unknown: { title: m.unknownTitle, body: m.unknownBody },
    failed: { title: m.failedTitle, body: m.failedBody },
    cancelled: { title: m.cancelledTitle, body: m.cancelledBody },
  }[state];

  return (
    <section aria-labelledby="result-title" className={`result ${state}`}>
      <h3 id="result-title" tabIndex={-1} ref={(el) => el?.focus()}>
        {copy.title}
      </h3>
      <p>{copy.body}</p>
      {state === "posted" &&
        (link ? (
          <p>
            <a href={link} target="_blank" rel="noopener noreferrer" className="primary linkbtn">
              {m.viewLog}
            </a>
          </p>
        ) : (
          <p>{m.postedNoLink}</p>
        ))}
      <button type="button" onClick={onAgain}>
        {m.again}
      </button>
    </section>
  );
}
