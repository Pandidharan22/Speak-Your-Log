import type { Messages } from "../messages";
import type { Line } from "../transcript";

export function Transcript({ m, lines }: { m: Messages; lines: readonly Line[] }) {
  return (
    <section aria-labelledby="transcript-title" className="transcript">
      <h3 id="transcript-title">{m.transcriptTitle}</h3>
      {/* role="log" announces new lines politely to screen readers; it doubles as captions. */}
      <div role="log" aria-live="polite" aria-relevant="additions text">
        {lines.length === 0 ? (
          <p className="hint">{m.emptyTranscript}</p>
        ) : (
          <ol>
            {lines.map((line) => (
              <li key={line.id} className={line.who} data-final={line.final}>
                <span className="who">{line.who === "student" ? m.you : m.agent}</span>
                <span lang="ta-IN" className="said">
                  {line.text}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
