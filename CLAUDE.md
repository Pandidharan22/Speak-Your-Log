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
| Database + user auth | Supabase (Postgres + Auth) |
| Frontend | React + TypeScript (Vite), static hosting |

## Security invariants (a violation is a bug, whatever the feature)

- The Proof token is **encrypted and decrypted server-side only** (FastAPI). It never reaches browser JS — not in responses, not in storage, not in logs, not in LiveKit metadata. The UI may show only "connected" + last 4 chars.
- Only FastAPI holds the encryption key. The agent worker and the browser never see the Proof token.
- **The LLM never writes the log.** The log is the student's verbatim transcript. The LLM only generates *questions* and classifies intent. No summarising, no rewriting, no "cleaning up".
- **The LLM never triggers a post.** Posting happens in deterministic code, only from the `CONFIRMING` state after a validated "yes, post it". Student speech is untrusted input (prompt injection).
- Supabase tables holding credentials: RLS on, no policies for `anon`/`authenticated`; accessed only by the backend with the service-role key.
- Posting is idempotent-by-design (Proof's limit is 20 posts/day/token): atomic state transition before the call, no blind retries on ambiguous failures.

## Planned repo layout

```
apps/web      React client (LiveKit client + UI)
apps/api      FastAPI: auth verify, LiveKit token mint, Proof-token vault, post gateway
apps/agent    LiveKit agent worker: interview state machine, STT/LLM/TTS wiring
docs/         PRD.md, SRS.md, Architecture.md, adr/, scale/ (SaaS-scale design)
Dev_Journal.md
```

## Docs plan (written one at a time, in this order)

PRD → SRS → Architecture.md → ADRs (`docs/adr/`) → Scale-out design (interview prep, `docs/scale/`) → test strategy → runbook/deploy checklist.

## Git workflow

- Branches: `main` (prod) ← PR ← `develop` (default working branch).
- Commit only on explicit user go-ahead. Journal entry = separate follow-up commit.
- Commit messages end with the attribution line required by the harness.

---

## Current Status

_Last updated: 2026-10-01_

**Phase:** 0 — Design & documentation (no application code yet).

**Done**
- Brief received and analysed; research on free-tier limits completed (see "Verified facts").
- Repo bootstrapped locally: `CLAUDE.md`, `.gitignore`. **Not yet committed** (awaiting user go-ahead). `git init` done; branches `main`/`develop` to be created at first commit.

**Next step:** user signs off on the design decisions in the chat → write PRD.md.

**Open decisions:** submission deadline; auth method (Google sign-in vs email magic link); API hosting target; agent pipeline (cascaded vs Gemini Live) — to be settled by the Phase 0 spike.

**Biggest risks**
1. **Tamil STT quality is unverified.** `gemini-3.5-transcribe` docs do not list Tamil; Live API language table is ambiguous. → Phase 0 spike on real Tamil/Tanglish audio *before* committing to a pipeline. STT sits behind an interface so it can be swapped.
2. Free-tier Gemini: content is used to improve Google products (privacy disclosure needed); limits can change; TTS 3.8 models free only through 2026-12-31.
3. LiveKit Build plan hard caps: 1 deployed agent, 1,000 agent-minutes/mo, 5 concurrent sessions; agents sleep when idle (cold start).
4. Supabase free projects pause after ~1 week of inactivity; no automatic backups.

## Verified facts (as of 2026-10-01; re-verify before relying on them)

- Gemini API free tier covers Flash/Flash-Lite text models, Live/native-audio models, `gemini-3.5-transcribe(-live)`, and TTS (`gemini-3.8-flash-tts`, free through 2026-12-31). Free-tier content may be used to improve Google products.
- Gemini TTS lists Tamil as supported; streams 24 kHz mono 16-bit PCM.
- Live API: audio-only sessions limited to 15 min (our sessions are ~2 min); `input_audio_transcription` / `output_audio_transcription` available.
- LiveKit Cloud Build plan: 5,000 WebRTC min, 1,000 agent-session min, 5 concurrent agent sessions, 1 agent deployment.
- Supabase Free: 500 MB DB, 50k MAU, 2 active projects, pauses after 1 week inactivity.
- Proof endpoint `POST /api/mcp` is JSON-RPC; unauthenticated calls return `-32001 Unauthorized`. `tools/list` (needs token) will reveal the full verb list.
