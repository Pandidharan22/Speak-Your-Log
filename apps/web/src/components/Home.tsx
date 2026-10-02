import type { Messages } from "../messages";

interface Props {
  m: Messages;
  last4: string;
  starting: boolean;
  error: string | null;
  onStart: () => void;
  onDisconnect: () => void;
}

export function Home({ m, last4, starting, error, onStart, onDisconnect }: Props) {
  return (
    <section aria-labelledby="home-title">
      <p className="connected" role="status">
        {m.connectedAs(last4)}{" "}
        <button type="button" className="link" onClick={onDisconnect}>
          {m.disconnect}
        </button>
      </p>
      <h2 id="home-title">{m.startTitle}</h2>
      <p>{m.startHelp}</p>
      <p className="notice">{m.privacyNotice}</p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button type="button" className="primary big" onClick={onStart} disabled={starting}>
        {starting ? m.starting : m.startButton}
      </button>
    </section>
  );
}
