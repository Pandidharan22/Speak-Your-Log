import { useCallback, useEffect, useState } from "react";
import { ApiError, type InterviewStart } from "./api";
import { ConnectForm } from "./components/ConnectForm";
import { Home } from "./components/Home";
import { Interview } from "./components/Interview";
import { realDeps, type Deps } from "./deps";
import { MESSAGES, errorText, type Lang } from "./messages";

type View =
  | { kind: "boot" }
  | { kind: "connect" }
  | { kind: "home"; last4: string }
  | { kind: "interview"; info: InterviewStart }
  | { kind: "unreachable"; code: string };

const LANG_KEY = "syl.lang";

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(LANG_KEY); // a harmless per-device preference
    if (saved === "en" || saved === "ta") return saved;
  } catch {
    /* storage blocked: fall through to the browser language */
  }
  return typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("ta") ? "ta" : "en";
}

export function App({ deps = realDeps }: { deps?: Deps }) {
  const [lang, setLang] = useState<Lang>(initialLang);
  const [view, setView] = useState<View>({ kind: "boot" });
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const m = MESSAGES[lang];

  const boot = useCallback(async () => {
    setView({ kind: "boot" });
    try {
      const status = await deps.api.session();
      setView(status.connected ? { kind: "home", last4: status.last4 ?? "" } : { kind: "connect" });
    } catch (e) {
      setView({ kind: "unreachable", code: e instanceof ApiError ? e.code : "network_error" });
    }
  }, [deps]);

  useEffect(() => {
    void boot();
  }, [boot]);

  useEffect(() => {
    document.documentElement.lang = lang === "ta" ? "ta" : "en";
    try {
      localStorage.setItem(LANG_KEY, lang);
    } catch {
      /* ignore */
    }
  }, [lang]);

  async function start() {
    setStarting(true);
    setStartError(null);
    try {
      setView({ kind: "interview", info: await deps.api.startInterview() });
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "unknown";
      if (code === "token_not_connected") setView({ kind: "connect" });
      else setStartError(errorText(m, code));
    } finally {
      setStarting(false);
    }
  }

  async function disconnect() {
    try {
      await deps.api.disconnect();
    } finally {
      setView({ kind: "connect" });
    }
  }

  return (
    <div className="shell">
      <header>
        <h1>{m.appName}</h1>
        <p className="tagline">{m.tagline}</p>
        <button type="button" className="link lang" onClick={() => setLang(lang === "en" ? "ta" : "en")}>
          {m.langSwitch}
        </button>
      </header>
      <main>
        {view.kind === "boot" && <p role="status">{m.statusConnecting}</p>}
        {view.kind === "unreachable" && (
          <section>
            <p role="alert" className="error">
              {errorText(m, view.code)}
            </p>
            <p className="hint">{m.waking}</p>
            <button type="button" className="primary" onClick={() => void boot()}>
              {m.again}
            </button>
          </section>
        )}
        {view.kind === "connect" && (
          <ConnectForm
            m={m}
            connect={deps.api.connectToken}
            onConnected={(s) => setView({ kind: "home", last4: s.last4 ?? "" })}
          />
        )}
        {view.kind === "home" && (
          <Home
            m={m}
            last4={view.last4}
            starting={starting}
            error={startError}
            onStart={() => void start()}
            onDisconnect={() => void disconnect()}
          />
        )}
        {view.kind === "interview" && (
          <Interview m={m} info={view.info} deps={deps} onAgain={() => void boot()} />
        )}
      </main>
    </div>
  );
}
