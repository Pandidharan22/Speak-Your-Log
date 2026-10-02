# Speak Your Log

A voice agent that talks with a student for about two minutes about their day, in **Tamil, English or a mix**, and saves what they said, **word for word**, as a log on their [Proof](https://proof.zeromaintenanceengineer.in) record. It posts only after the student says "yes, post it".

> Built as a take-home for Vruksha Consultancy. **Live demo:** _(link added at deploy: see [DEPLOY.md](DEPLOY.md))_ · **Build record:** [Dev_Journal.md](Dev_Journal.md) · **Plan and progress:** [Execution_plan.md](Execution_plan.md)

## What it does

1. The student connects their Proof token once (stored encrypted, remembered on that device).
2. The agent asks three questions: *What did you try today? What broke? Why did you choose that?*
3. It asks **one** follow-up built from the student's own words (*"You said you switched to an IR sensor. Why that over the ultrasonic one?"*).
4. It reads the log back, and shows the exact text on screen with a notice that it will be public.
5. Only when the student says "yes, post it" does the backend post it to Proof. Nothing else can.

## The decisions that matter

| Decision | Why | Record |
|---|---|---|
| **Gemini Live** for the conversation, not STT → LLM → TTS | Measured: batch transcription alone took 3–5 s per turn; Live answers in about 1–2 s | [ADR-001](docs/adr/001-conversation-pipeline.md) |
| The model is **pre-armed, not steered** | Gemini Live replies *before* the student's transcript arrives (measured: reply audio +2.4 s, transcript +6.5 s), so in-the-moment control is impossible | [ADR-008](docs/adr/008-gemini-live-turn-control.md) |
| **The agent proposes, the backend disposes** | The agent never holds the Proof token or the encryption key and cannot supply post text; the backend builds the post from stored answers | [ADR-005](docs/adr/005-consent-and-post-gateway.md) |
| **Consent is code, not model behaviour** | A state machine, an atomic `confirming → posting` update, and a rule-based (no LLM) yes/no classifier for English, Tamil and Tanglish | [ADR-005](docs/adr/005-consent-and-post-gateway.md) |
| The log is **verbatim** | The only transformation is trimming the ends of each answer; no labels, no rewriting | [ADR-007](docs/adr/007-verbatim-record-policy.md) |
| Token encrypted with **AES-256-GCM**, bound to its owner | A database leak alone yields nothing usable | [ADR-004](docs/adr/004-token-encryption.md) |
| **Device-session cookie**, not accounts | The only Supabase project is shared with another live app; no email infrastructure needed | [ADR-002](docs/adr/002-device-session-identity.md) |
| **Isolated schema + limited role** in the shared Supabase project | Blast radius of any bug is this app's four tables | [ADR-003](docs/adr/003-database-isolation.md) |

Requirements and design: [PRD](docs/PRD.md) · [SRS](docs/SRS.md) · [Architecture](docs/Architecture.md) · [all ADRs](docs/adr/README.md).

## Architecture in one picture

```
Browser (React) ──HTTPS+cookie──▶ FastAPI (Render) ──▶ Supabase Postgres (own schema)
     │                               │  ▲                      
     │ WebRTC audio                  │  │ job token (HMAC, one interview)
     ▼                               ▼  │
LiveKit Cloud (SFU) ◀──────────── Voice agent (LiveKit Cloud) ◀──▶ Gemini Live
                                     FastAPI ──post_log──▶ Proof
```

Each component holds only the secrets it needs: the browser holds none, the agent holds the Gemini key and LiveKit credentials, and only the API holds the database URL, the encryption key and (decrypted, in memory, at post time) the Proof token.

## Run it locally

Prerequisites: Python 3.12, Node 22, and a filled-in `.env` (copy `.env.example`; every variable is documented there). You need a Gemini API key, a LiveKit Cloud project, and a Supabase Postgres role created with [`db/migrations/0001_init.sql`](db/migrations/0001_init.sql).

```bash
# API (port 8000)
cd apps/api && python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m uvicorn app.main:create_app --factory --reload --port 8000

# Voice agent (separate terminal)
cd apps/agent && python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python agent.py start

# Web app (separate terminal; proxies /api to :8000)
cd apps/web && npm ci && npm run dev        # http://localhost:5173
```

## Tests

| Part | Command | What it covers |
|---|---|---|
| API (564) | `cd apps/api && .venv/Scripts/python -m pytest` | config, sessions, vault crypto, Proof client, token endpoints, interview start, post gateway, internal endpoints. Integration tests use the real isolated schema in rolled-back transactions and skip when there is no `DATABASE_URL` |
| Agent (411) | `cd apps/agent && .venv/Scripts/python -m pytest` | interview state machine (every reachable state explored), consent classifier (135-case table), driver, read-back verification, lifecycle, API client |
| Web (77) | `cd apps/web && npm test` | API client, screens, accessibility roles, source-level security rules |

Beyond ordinary tests, every security-relevant module was **mutation-tested**: deliberate bugs (token logged, consent granted on silence, a double post, a wrong state transition…) are injected and the suite must catch each one. The journal records what survived and the test that was added in response.

## Security notes

- The Proof token never reaches the browser after it is typed once: no response, log, storage or URL carries it (tested at every layer).
- The post gateway posts only from `confirming`, at most once, with text built from the stored answers; an ambiguous Proof outcome (timeout, 5xx) is never retried.
- Student speech is untrusted input: it can never trigger a post, and the consent check cannot be talked into confirming.
- Free-tier Gemini content may be used by Google to improve its products; the page says so before the first word is spoken. This app stores no audio.

## Free-tier limits and known limitations

- LiveKit Build plan: 1 deployed agent, 5 concurrent sessions, 1,000 agent-minutes a month (about 500 two-minute demos); the API refuses new interviews above 4 concurrent.
- Render's free web service sleeps after 15 minutes idle; the first visit can take about a minute (the page says so).
- Identity is per device: clearing cookies means pasting the token again.
- `gemini-3.1-flash-live-preview` is a preview model; the model id is configuration, with `gemini-3.8-live` as the fallback.
- Reply latency is about 2.3 s after the student stops speaking, mostly Gemini's end-of-turn silence wait.
- Short answers are not re-prompted live (Gemini replies before the transcript is available); the read-back lets the student redo anything.
- The read-back is **verified** against the model's own transcript before any "yes" can count; if the model fails to read the log, it is repeated once and then the session ends without posting (see the ADR-008 addendum for the live findings behind this).
- The model's automatic reply and the system's explicit one can both announce the outcome, so the student may hear it twice. Cosmetic; the content is always accurate.
- The preview Live model sometimes ends a session with a provider-side error; the app then stops without posting.
- Tested so far with synthetic speech and a stand-in Proof endpoint; the first real-voice run and the first real post are described in [DEPLOY.md](DEPLOY.md) (the "real-voice test").

## Repository map

```
apps/api      FastAPI: identity, token vault, Proof client, interview start, post gateway
apps/agent    LiveKit agent worker: interview flow, consent, Gemini Live wiring
apps/web      React client
db/           the isolated schema, rollback, and verification scripts
docs/         PRD, SRS, Architecture, ADRs, deploy checklist, Proof build-log draft
spike/        the Phase 0 experiments (Tamil recognition, Live API latency) and RESULTS.md
```
