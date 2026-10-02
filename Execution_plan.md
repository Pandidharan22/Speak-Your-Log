# Execution Plan — Speak Your Log

Progress tracker. **Deadline: 2026-10-02 23:59.** Working budget ≈ 10–12 focused hours.

**How this file works.** Each step is one unit of work: do it → verify it → you give the go-ahead → commit → tick the box here (`[x]` + commit hash) → journal entry as its own commit (see [Dev_Journal.md](Dev_Journal.md)). One step at a time; no jumping ahead.

`[x]` done · `[ ]` to do · `[~]` in progress · **⚑ CUT** = first thing to drop if we run late.

**Current step → owner actions: Render, lk agent create, real-voice test, post build log, PR to main**

---

## Phase 0 — Foundation & de-risking ✅

- [x] 0.1 Repo, `main`/`develop`, `CLAUDE.md`, strict `.gitignore` — `c7d4622`
- [x] 0.2 `.env.example`; user fills local `.env`; auth/DB/hosting decisions recorded — `2650b1e`
- [x] 0.3 Isolated Supabase schema + limited `syl_app` role; 14/14 isolation checks pass — `f23bfcc`
- [x] 0.4 Spike: Tamil STT, latency, Live API, Proof schema → pipeline decision — `6d79ef5`

## Phase 1 — Lean documentation (≈1 h)

- [x] 1.1 `Execution_plan.md` (this file) — `1cb6cde`
- [x] 1.2 `docs/PRD.md` — what we build and why, acceptance criteria — `001185e`
- [x] 1.3 `docs/SRS.md` — numbered requirements, API, state machine, error matrix — `001185e`
- [x] 1.4 `docs/Architecture.md` — components, flows, security model, deployment — `5626aa5`
- [x] 1.5 `docs/adr/` ADR-001…007 — one page each, options and trade-offs — `5626aa5`

## Phase 2 — Backend core: identity, vault, Proof client (≈2.5 h)

- [x] 2.1 FastAPI scaffold: settings from env, `/healthz`, ruff + pytest, CI workflow; add `AGENT_JOB_SECRET` to `.env.example` (user generates it; separate key per purpose) — `3287fc8`
- [x] 2.2 DB layer: pooled psycopg (transaction-pooler safe), typed queries — `9347d25`
- [x] 2.3 Device session: cookie issue/verify (HMAC hash in DB), CSRF/origin check — `3e35b15`
- [x] 2.4 Token vault crypto: AES-256-GCM, AAD = user_id, key-id rotation + tests (round-trip, tamper, wrong user, wrong key) — `22ea828`
- [x] 2.5 Proof client: `tools/list` validation, `post_log`, typed errors (401/429/timeout) + mocked tests — `bec1ff1`
- [x] 2.6 Token endpoints: `PUT/GET/DELETE /api/proof-token` + tests proving the token never appears in any response or log — `1fd6845`

## Phase 3 — Voice agent (≈3.5 h) — the core of the product

- [x] 3.1 Interview state machine (pure Python) + exhaustive unit tests — `c1bbe2f`
- [x] 3.2 `POST /api/interviews`: create room, explicit agent dispatch, LiveKit token for the browser — `978ce68`
- [x] 3.3 Hello-agent: joins room, Gemini Live (`gemini-3.1-flash-live-preview`), speaks Tamil/English locally — `e433a65`
- [x] 3.4 Scripted questions Q1 → Q2 → Q3 + verbatim capture per turn — `df71b4e`
- [ ] 3.5 Follow-up: one question that quotes the student's own words (+ small eval set)
- [x] 3.6 Consent classifier: rules first, LLM fallback; Tamil/English/Tanglish eval set — `0916ae1`
- [x] 3.7 Read-back (spoken + on-screen data message) and `confirming` state — `6ec5984`
- [x] 3.8 Agent → API internal call with signed job token — `6ec5984`
- [ ] 3.9 ⚑ CUT: per-utterance re-transcription with `gemini-3.5-transcribe` for higher fidelity

## Phase 4 — Post gateway (≈0.75 h)

- [x] 4.1 `confirming → posting` atomic transition, daily-limit guard, `post_log` call, store URL; lazy purge of drafts older than 24 h (SRS NFR-3) — `7dabfa5`
- [x] 4.2 Failure-injection tests: Proof timeout → `post_unknown` (no blind retry), 401 → "reconnect token", 429 → friendly message, double-submit → one post — `7dabfa5`

## Phase 5 — Web UI (≈2 h)

- [x] 5.1 Vite + React + TS scaffold, LiveKit client, same-origin API calls — `1418698`
- [x] 5.2 "Connect Proof" screen (password-type field, cleared on submit, shows only `••••last4`) — `1418698`
- [x] 5.3 Interview screen: mic permission, status, live transcript — `1418698`
- [x] 5.4 Read-back panel: exact text, "this will be public on your Proof profile", Google-data disclosure, post result link — `1418698`
- [ ] 5.5 ⚑ CUT: `design:accessibility-review` + `design:ux-copy` pass (Tamil + English strings)

## Phase 6 — Deploy (≈1.5 h)

- [x] 6.1 Dockerfile (API + built UI) and agent Dockerfile — `190909b`
- [ ] 6.2 Render: API + static UI on one service; env vars set; keep-warm ping
- [ ] 6.3 LiveKit Cloud: `lk agent deploy`; secrets via platform, not the repo
- [ ] 6.4 **Real-voice end-to-end test on the hosted URL** (Tamil, Tanglish, English) — the validation the spike could not do
- [ ] 6.5 `engineering:deploy-checklist`; PR `develop` → `main`; tag `v1.0.0`

## Phase 7 — Submission

- [x] 7.1 `README.md`: what it is, live link, how to run, architecture pointer, limits of the free tier — `64fca46`
- [x] 7.2 `engineering:code-review` + security review of the whole diff; fix findings — `cfb85e4`
- [ ] 7.3 Post our own build log to Proof (what we tried / what broke / what we decided and why) — dogfooding, with the user's go-ahead
- [ ] 7.4 Submit: hosted link + repo link

## Phase 8 — Interview prep (parallel / after submission)

- [x] 8.1 `docs/scale/System_Design_Millions.md` — SaaS design for millions of concurrent users
- [x] 8.2 Capacity math + cost per minute + failure-mode drills
- [x] 8.3 Tradeoff cheat-sheet (the ADRs, condensed)

---

> **Reordered 2026-10-02 (dependency-driven):** the live agent cannot read the log back until the API holds the answers and builds the exact text, so the order is 3.6 (consent classifier) → 4.1 (post gateway + internal endpoints, which is also the API half of 3.8) → 3.7/3.8 (agent read-back, confirm, internal calls) → 4.2 → Phase 5. **3.5 (follow-up eval set) moves after the critical path** (quality work that unblocks nothing). Also: the live agent has no short-answer re-prompt (ADR-008).

## Cut order if time runs short

1. 3.9 re-transcription · 2. 5.5 a11y/copy polish · 3. 8.x (prep, not graded) · 4. CI workflow in 2.1.
**Never cut:** 2.4 (crypto tests), 4.1/4.2 (consent + idempotency), 6.4 (real-voice test), 7.1 (README).
