# CLAUDE.md — Speak Your Log

Voice agent that talks to a student for ~2 minutes about their day, transcribes their answers **word for word**, reads the log back, and — only after the student says "yes, post it" — posts it to their record on **Proof** via Proof's `post_log` connector (MCP-style JSON-RPC endpoint).

Take-home assignment from Vruksha Consultancy. Deliverables: a **hosted working agent (link) + the repo**, and a build log on Proof ("we read the record, not just the result").

---

## Non-negotiable process rules

These are strictly followed for the entire project.

1. **Plan first, one step at a time.** Build a detailed plan before starting a phase, then execute it one step at a time — don't jump ahead or batch unrelated steps together.
2. **Verify before commit.** After each step, verify it actually works / is correct / is good (run it, test it, read it back critically) before the user is asked to sign off. The user gives an explicit go-ahead to commit — **never commit unprompted**.
3. **Dev journal per commit.** After each step's work is committed, add an entry to [Dev_Journal.md](Dev_Journal.md) describing what was done and why, as its own separate commit.
4. **No secrets, ever.** `.gitignore` must stay proper; `.env` (and any real credentials/keys) must never be committed or hardcoded in source. Use `.env.example` for documenting required variables with placeholder values.
5. **Use skills deliberately.** Use the `engineering:*` skills (architecture, code-review, testing-strategy, system-design, tech-debt, deploy-checklist, debug) throughout implementation work. Use the `design:*` skills (design-critique, accessibility-review, ux-copy, design-system) for UI/UX work.
6. **Quality bar: L6+ Google SDE.** Every change should reflect senior-engineer judgment — correct, secure, well-tested, appropriately simple (no premature abstraction, no unnecessary complexity), with clear reasoning behind architectural choices. Prefer fewer, well-considered changes over broad speculative ones.
7. **Separate development from production.** Two long-lived Git branches: `main` (production) and `develop` (building & testing). Work happens on `develop` (or short-lived feature branches off it); `develop` merges into `main` via PR only.
8. **This file is the efficient-context anchor.** Keep the "Current Status" section below up to date after every meaningful step so a fresh session can orient without re-reading the whole codebase.

---

## Stack (free tier only)

| Concern | Choice |
|---|---|
| Backend API | Python 3.12 + FastAPI |
| Realtime media (SFU) + agent runtime | LiveKit (Cloud Build plan) + `livekit-agents` (Python) |
| STT / LLM / TTS | Gemini via Google AI Studio free tier (exact models fixed by Phase 0 spike) |
| Database | Supabase Postgres only (existing "Anthaathi" project, isolated schema + limited role). **Supabase Auth is NOT used.** |
| User identity | Own device-session cookie (httpOnly, Secure, SameSite=Lax; only an HMAC hash stored in DB) |
| Frontend | React + TypeScript (Vite), static hosting |

## Security invariants (a violation is a bug, whatever the feature)

- The Proof token is **encrypted and decrypted server-side only** (FastAPI). It never reaches browser JS — not in responses, not in storage, not in logs, not in LiveKit metadata. The UI may show only "connected" + last 4 chars.
- Only FastAPI holds the encryption key. The agent worker and the browser never see the Proof token.
- **The LLM never writes the log.** The log is the student's verbatim transcript. The LLM only generates *questions* and classifies intent. No summarising, no rewriting, no "cleaning up".
- **The LLM never triggers a post.** Posting happens in deterministic code, only from the `CONFIRMING` state after a validated "yes, post it". Student speech is untrusted input (prompt injection).
- Supabase tables live in schema `speakyourlog` (not exposed to PostgREST), RLS on with no policies for `anon`/`authenticated`; accessed only by the backend via the limited `syl_app` role. The project's `service_role` key is never used or stored by this app.
- Posting is idempotent-by-design (Proof's limit is 20 posts/day/token): atomic state transition before the call, no blind retries on ambiguous failures.

## Planned repo layout

```
apps/web      React client (LiveKit client + UI)
apps/api      FastAPI: auth verify, LiveKit token mint, Proof-token vault, post gateway
apps/agent    LiveKit agent worker: interview state machine, STT/LLM/TTS wiring
docs/         PRD.md, SRS.md, Architecture.md, adr/, scale/ (SaaS-scale design)
Dev_Journal.md
```

## Commands

API (from `apps/api`; own venv at `apps/api/.venv`, deps in `requirements*.txt`):

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt   # once
.venv/Scripts/python -m pytest                       # tests (integration tests need DATABASE_URL in .env; they roll back and skip in CI)
.venv/Scripts/python -m ruff check . && .venv/Scripts/python -m ruff format --check .   # lint + format
.venv/Scripts/python -m uvicorn app.main:create_app --factory --reload --port 8000     # run (reads repo-root .env)
```

Agent (from `apps/agent`; own venv, only pytest+ruff until step 3.3 adds `livekit-agents`): `.venv/Scripts/python -m pytest` · `.venv/Scripts/python -m ruff check .`

DB isolation check (from repo root, using the root `.venv` that also holds the spike deps): `.venv/Scripts/python db/verify_isolation.py`.
CI (`.github/workflows/ci.yml`): repo-hygiene guard (no `.env`/keys tracked) + API lint/format/tests.

## Docs plan (written one at a time, in this order)

PRD → SRS → Architecture.md → ADRs (`docs/adr/`) → Scale-out design (interview prep, `docs/scale/`) → test strategy → runbook/deploy checklist.

## Git workflow

- Branches: `main` (prod) ← PR ← `develop` (default working branch).
- Commit only on explicit user go-ahead. Journal entry = separate follow-up commit.
- Commit messages end with the attribution line required by the harness.

---

## Current Status

_Last updated: 2026-10-01_

**DEADLINE: 2026-10-02 23:59 (user's local time).** Real working budget is only ~10–12 hours (user also sleeps/works) and Claude usage limits apply → scope is the thinnest vertical slice that satisfies the brief; docs are lean; scale-out design is interview prep and comes after the deployed demo works. Deploy something working early; polish later.

**Phase:** 0, 1, 2 complete (Phase-2 review deferred until after Phase 4 — one pass over the whole system). **Phase 3 in progress:** 3.1–3.4 done. 3.4 = the scripted interview runs live: `interview/script.py` (pre-armed per-step instructions), `interview/driver.py` (re-arms the model after each answer, speaks explicitly only at branches; records answers verbatim via `Flow(min_words=0)`), `agent.py` wired to `conversation_item_added`. KEY FINDING (ADR-008): Gemini Live replies before the student's transcript arrives (reply audio +2.4 s, transcript +6.5 s after speech ends) so the model is *pre-armed*, never steered in the moment; live short-answer re-prompt dropped. Instructions are set at agent creation (an update right before the greeting loses it). 365 API + 124 agent tests.

**Autonomy mode (from 2026-10-02):** user is away; work proceeds through Execution_plan.md step by step with commit → journal commit → push after each step (standing permission). **Never without the user:** merge to `main`, deploy, post to Proof, touch their accounts/browser sessions, spend money. Helper: `finish_step.py` (scratchpad).

**Done**
- Bootstrap committed: `main` @ `c7d4622`; `develop` has the journal + setup commits. Work happens on `develop`.
- User approved design decisions D1–D8 (cascaded pipeline unless spike says Live is as good for Tamil; agent proposes / backend disposes; consent in code; AES-256-GCM token vault; idempotent post gateway).
- Local `.env` filled by user (Gemini, LiveKit, Proof spike token, two dev crypto keys, `DATABASE_URL` for the limited `syl_app` role — all validated, never printed).
- **DB live and verified** in the shared "Anthaathi" Supabase project: `db/migrations/0001_init.sql` run by the user; admin check (`db/verify_admin.sql`) matched; `db/verify_isolation.py` passes 14/14 (app can use only schema `speakyourlog`; denied on auth/storage/vault/public). Rollback: `db/rollback/0001_init_down.sql`.
- **Spike done** (`spike/RESULTS.md`): Tamil works in Gemini; batch STT is 3–5 s/utterance (too slow); **Gemini Live `gemini-3.1-flash-live-preview` ≈1 s first-audio latency** and asks the quoting follow-up natively.
- Proof `post_log` schema captured: 18 verbs, `why` required for `decided`, **posts are public** on the profile.

**Decisions made 2026-10-01**
- **Supabase:** no new project possible (free limit hit). Reuse the user's existing project "Anthaathi" with strict isolation: dedicated schema `speakyourlog`, dedicated limited Postgres role `syl_app`, RLS on, schema NOT exposed to PostgREST, connect via the pooler URL. We never receive the project's `service_role` key (it would bypass RLS for all Anthaathi data).
- **Auth (user ACCEPTED):** device-session cookie instead of magic link — magic link conflicts with the shared project (built-in SMTP = 2 mails/hour & team members only; custom SMTP / templates / `auth.users` are project-wide and would affect Anthaathi). Magic link is the documented upgrade path (ADR).
- **Hosting (no card):** one FastAPI service serving the built React app on Render free (sleeps after 15 min → keep-warm ping); agent on LiveKit Cloud Build (1 deployment). Hugging Face Spaces dropped (new compute Spaces need a paid plan).
- **Spike without recorded audio (user's call, time-boxed):** use Gemini TTS to synthesise Tamil / Tanglish / English audio with known text, feed it to the STT candidates, compare. Caveat: clean synthetic audio overstates accuracy; real-voice validation happens in the first live end-to-end test.

**Decision D1 RESOLVED:** conversation = Gemini Live (`gemini-3.1-flash-live-preview`, fallback `gemini-3.8-live`; model ids are env config). Record path = Live input transcription, optionally refined per utterance by `gemini-3.5-transcribe` off the latency-critical path. Consent/post stay in deterministic code; read-back text is also shown on screen.
- Product mapping: one Proof log per session, verb `built`; `content` = student's verbatim answers to Q1 ("tried") + Q2 ("broke"); `why` = verbatim answers to Q3 ("why") + follow-up. No labels, no rewriting.

**Progress tracker:** [Execution_plan.md](Execution_plan.md) — every step has a checkbox; tick it (with the commit hash) when the step is verified and committed. Read it first in a fresh session.

**Committed so far:** Phase 0 (repo, env, DB, spike) — `8cfba87` on `develop`. Phase 1 docs committed (`Execution_plan.md`, `docs/PRD.md`, `docs/SRS.md`, `docs/Architecture.md`, `docs/adr/001–007`); commit hashes are recorded in `Execution_plan.md`.

**Next step:** 3.5 follow-up quality eval set (quotes the student's own words; Tamil/English). Known: Q2 stays English for Tamil speakers. The vertical slice order is: backend core (identity, vault, Proof client) → voice agent → post gateway → UI → deploy → real-voice test → README/submit.

**Open decisions:** none blocking.

**Biggest risks**
1. **Real-voice Tamil quality is still unverified** (spike used clean synthetic speech). Mixed Tamil/English output script is unstable → pin it with an explicit instruction. `gemini-3.1-flash-live-preview` is a *preview* model → keep model id configurable, fallback `gemini-3.8-live` (romanises Tamil, slower).
2. Free-tier Gemini: content is used to improve Google products (privacy disclosure needed); limits can change; TTS 3.8 models free only through 2026-12-31.
3. LiveKit Build plan hard caps: 1 deployed agent, 1,000 agent-minutes/mo, 5 concurrent sessions; agents sleep when idle (cold start).
4. Supabase free projects pause after ~1 week of inactivity; no automatic backups.

**Unverified until the first real post (6.4):** the *success* reply shape of `post_log` (the client parses it tolerantly).

## Verified facts (as of 2026-10-01; re-verify before relying on them)

- Gemini API free tier covers Flash/Flash-Lite text models, Live/native-audio models, `gemini-3.5-transcribe(-live)`, and TTS (`gemini-3.8-flash-tts`, free through 2026-12-31). Free-tier content may be used to improve Google products.
- Gemini TTS lists Tamil as supported; streams 24 kHz mono 16-bit PCM.
- Live API: audio-only sessions limited to 15 min (our sessions are ~2 min); `input_audio_transcription` / `output_audio_transcription` available.
- LiveKit Cloud Build plan: 5,000 WebRTC min, 1,000 agent-session min, 5 concurrent agent sessions, 1 agent deployment.
- Supabase Free: 500 MB DB, 50k MAU, 2 active projects, pauses after 1 week inactivity.
- Proof endpoint `POST /api/mcp` is JSON-RPC; unauthenticated calls return `-32001 Unauthorized`. `tools/list` (needs token) will reveal the full verb list.
