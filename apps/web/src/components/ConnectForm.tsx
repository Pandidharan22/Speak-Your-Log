import { useId, useState, type FormEvent } from "react";
import { ApiError, type Status } from "../api";
import { errorText, type Messages } from "../messages";

interface Props {
  m: Messages;
  connect: (token: string) => Promise<Status>;
  onConnected: (status: Status) => void;
}

// The only place a Proof token is ever typed. It lives in this component's memory for as long as
// the person is typing: the field is emptied the moment the form is submitted, the value is never
// written to storage or the URL, never logged, and the server never sends it back.
export function ConnectForm({ m, connect, onConnected }: Props) {
  const inputId = useId();
  const hintId = useId();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !token.trim()) return;
    const submitted = token;
    setToken(""); // gone from the page immediately, whatever happens next
    setBusy(true);
    setError(null);
    try {
      onConnected(await connect(submitted));
    } catch (e) {
      setError(errorText(m, e instanceof ApiError ? e.code : "unknown"));
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="connect-title">
      <h2 id="connect-title">{m.connectTitle}</h2>
      <p>{m.connectHelp}</p>
      <p>
        <a
          href="https://proof.zeromaintenanceengineer.in/settings/mcp"
          target="_blank"
          rel="noopener noreferrer"
        >
          {m.connectLink}
        </a>
      </p>
      <form onSubmit={submit} noValidate>
        <label htmlFor={inputId}>{m.tokenLabel}</label>
        <input
          id={inputId}
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          aria-describedby={hintId}
          aria-invalid={error ? true : undefined}
          disabled={busy}
        />
        <p id={hintId} className="hint">
          {m.tokenHint}
        </p>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button type="submit" className="primary" disabled={busy || !token.trim()}>
          {busy ? m.connecting : m.connectButton}
        </button>
      </form>
    </section>
  );
}
