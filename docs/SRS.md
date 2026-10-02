# SRS — Speak Your Log

**Status:** Accepted · **Date:** 2026-10-01 · Companion to [PRD.md](PRD.md) and [Architecture.md](Architecture.md). Keywords MUST / SHOULD follow RFC 2119.

## 1. Functional requirements

### Identity & Proof connection
| ID | Requirement |
|---|---|
| FR-1 | On first visit the system MUST create an anonymous user and issue a device-session cookie (httpOnly, Secure, SameSite=Lax, ≥128-bit random, 90-day lifetime; `__Host-` prefixed in production). Only an HMAC-SHA256 of the cookie is stored. New-user creation is limited to 10 per minute per IP, and state-changing `/api` requests must carry the app's own `Origin`. |
| FR-2 | The user MUST be able to submit their Proof token once. The system MUST validate it against Proof (`tools/list` must succeed and expose `post_log`) before storing it. |
| FR-3 | The token MUST be stored only as AES-256-GCM ciphertext (random 12-byte nonce, AAD = user id, key id recorded). Plaintext MUST exist only in server memory during validate/post. |
| FR-4 | No API response, log line, cookie, local/session storage entry, or LiveKit metadata MAY contain the token. The UI MAY show only "connected" and the last 4 characters. |
| FR-5 | The user MUST be able to disconnect (delete) the stored token. |

### Interview
| ID | Requirement |
|---|---|
| FR-6 | The agent MUST greet and ask, in order: Q1 "What did you try today?", Q2 "What broke?", Q3 "Why did you choose that?", in the language the student uses (Tamil or English; mixed speech accepted). |
| FR-7 | After Q3 the agent MUST ask exactly **one** follow-up that quotes a specific phrase from the student's answers. |
| FR-8 | Every student answer MUST be stored as the transcribed text of what was said — no summarising, rewriting, translating, or grammar correction. Script choice for mixed speech is pinned by instruction (Tamil words in Tamil script, English words in Latin). |
| FR-9 | If an answer is empty or < 3 words, the agent SHOULD ask once for more detail, then accept what is given. **Not met in the live agent:** Gemini Live replies before the transcript is available, so this cannot be decided in time (ADR-008); the read-back (FR-12) and "start over" (FR-17) cover thin answers. The behaviour exists and is tested in `Flow` for transports that allow it. |
| FR-10 | The session MUST end gracefully on student cancel, inactivity (30 s of silence after a prompt), or a hard cap of 5 minutes. |
| FR-11 | The agent MUST NOT ask for or accept secrets (tokens, passwords) by voice. |

### Read-back, consent, post
| ID | Requirement |
|---|---|
| FR-12 | After the follow-up the system MUST read the log back aloud and show the **exact** text on screen, with a notice that it will be public on the student's Proof profile and that Gemini free-tier data may be used by Google to improve its products. |
| FR-13 | The session MUST enter `confirming` and wait for an intent classified as `confirm` ("yes, post it" and Tamil/Tanglish equivalents). `edit`, `cancel`, `unclear`, or silence MUST NOT post. |
| FR-14 | The post payload MUST be built server-side from the stored draft: `verb = "built"`, `content = Q1 + " " + Q2`, `why = Q3 + " " + follow-up answer`. The agent MUST NOT be able to supply different text at post time. |
| FR-15 | Posting MUST use an atomic `confirming → posting` transition so a session posts at most once. |
| FR-16 | On success the system MUST store and show the Proof log URL. |
| FR-17 | On `edit`, the system SHOULD offer to start the interview again (draft cleared); on `cancel`, discard the draft. |

## 2. Interview state machine

Persisted state (`interview_sessions.state`, enforced by a CHECK constraint):

```
created ──agent joins──▶ interviewing ──read-back done──▶ confirming ──valid "yes"──▶ posting ──ok──▶ posted
   │                         │  ▲                            │  │                         ├─timeout/unknown─▶ post_unknown
   │                         │  └────── edit: start over ────┘  │                         └─Proof error────▶ failed
   └────────── cancel / timeout / hard cap (from any non-terminal state except posting) ──▶ cancelled
```

Two more moves: `posting → confirming` when Proof **definitely did not apply** the post (bad token, rate limit, unreachable) so the student can be asked to confirm and retry (at most twice in total); and `posting` can never be cancelled, it must resolve to `posted`, `post_unknown`, `failed`, or back to `confirming`.

Conversation steps inside `interviewing` (agent-local): `greeting → q1 → q2 → q3 → followup → readback`, then `confirm`. Terminal states: `posted`, `post_unknown`, `failed`, `cancelled`. **The only path to `posting` is from `confirming`**, and a spoken "yes" only counts after the read-back has finished. Implemented as two pure modules, both exhaustively tested: `apps/api/app/states.py` (persisted table) and `apps/agent/interview/flow.py` (conversation flow; its tests explore all 753 reachable states and check the consent invariants on every transition).

## 3. Interfaces

### 3.1 HTTP API (same origin; JSON; cookie-authenticated unless noted)
| Method & path | Purpose | Notes |
|---|---|---|
| `GET /healthz` | Liveness | No auth |
| `POST /api/session` | Idempotent bootstrap: ensure a device user + cookie | Returns `{connected, last4}` |
| `PUT /api/proof-token` | Validate and store token | Body `{token}`; 10 attempts/min per IP and per user; never echoed. Errors: `422 invalid_token_format`, `400 token_rejected` / `token_cannot_post`, `429 proof_rate_limited` / `too_many_attempts`, `503 proof_unavailable`. Validation errors (422) never repeat the input. Bodies over 16 KB get 413 |
| `GET /api/proof-token` | Connection status | `{connected, last4}` only |
| `DELETE /api/proof-token` | Disconnect | |
| `POST /api/interviews` | Create interview, create room, **explicitly dispatch** the agent, mint browser LiveKit token | Requires connected token; returns `{livekit_url, token, interview_id}` |
| `GET /api/interviews/{id}` | State + result URL | Owner only |
| `POST /internal/interviews/{id}/state` | Agent reports `interviewing` / `confirming` / `cancelled` | HMAC job token |
| `POST /internal/interviews/{id}/answers` | Agent stores one verbatim answer | HMAC job token; only in `interviewing` |
| `POST /internal/interviews/{id}/post` | Agent signals a validated confirmation; backend posts | HMAC job token; only from `confirming` |

The job token is an HMAC over `interview_id|expiry` (short-lived, scoped to one interview) passed to the agent through **server-side** dispatch metadata — never through the browser's LiveKit token.

### 3.2 External
- **LiveKit**: browser ↔ room (WebRTC audio); agent worker joins the room; UI state/transcripts travel over the room data channel.
- **Gemini Live**: bidirectional audio + input/output transcription; model ids from env.
- **Proof**: JSON-RPC `tools/call` → `post_log {verb, content, why?, evidence_url?}`; `tools/list` for validation. Verbs, limits, and the "public log" rule are recorded in `spike/RESULTS.md`.
- **Postgres** (Supabase pooler): role `syl_app`, schema `speakyourlog` — see `db/migrations/0001_init.sql`.

## 4. Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-1 | Latency | p50 time from end of student speech to first agent audio ≤ 1.5 s; p95 ≤ 3 s (spike: 0.9–1.1 s) |
| NFR-2 | Security | Cookies httpOnly+Secure+SameSite; state-changing routes check `Origin`; token endpoint rate-limited (≤10/min/IP); secrets only from env; no secret in logs; dependency versions pinned |
| NFR-3 | Privacy | No audio stored. Drafts hold text only and are purged ≤ 24 h after the session ends. Logs carry interview id, never transcript text or tokens. Disclosure shown before the first session. |
| NFR-4 | Reliability | Bounded retries with jitter on Gemini calls (≤2); every external failure has a spoken or on-screen fallback; no unbounded loops |
| NFR-5 | Availability | Best effort on free tiers; cold start communicated in the UI |
| NFR-6 | Accessibility | WCAG 2.1 AA: keyboard-operable, visible focus, live transcript as captions, contrast ≥ 4.5:1, status announced via ARIA live region |
| NFR-7 | Compatibility | Latest Chrome, Edge, Safari, and Chrome on Android (microphone + WebRTC required) |
| NFR-8 | Testability | State machine and crypto are pure modules with unit tests; Proof and Gemini are behind interfaces and faked in tests |
| NFR-9 | Cost | Free tiers only; session minutes counted against LiveKit's 1,000/month cap |

## 5. Error handling matrix

| Situation | System behaviour |
|---|---|
| Proof token invalid / revoked (401) | Mark disconnected; UI asks to reconnect; draft kept until session ends |
| Proof rate limit (429) or our daily guard (20/24 h) | State `failed`; message: daily limit reached, try tomorrow; draft not lost until purge |
| Proof timeout / network error mid-post | State `post_unknown`; **no automatic retry**; user told to check their Proof profile, with a manual "check again" |
| Gemini 503/429 | ≤2 retries; then spoken "let's try again in a moment" and offer restart |
| Mic denied / no mic | Clear on-screen instruction; no session created |
| LiveKit agent not available (cold / cap reached) | "Agent is waking up" state; timeout after 45 s with retry button |
| Duplicate post request | Second `confirming → posting` update affects 0 rows → returns the existing result |
| Student says unrelated/adversarial text | Treated as an answer or `unclear`; never executes instructions; can never trigger a post |

## 6. Data

Tables in schema `speakyourlog`: `users`, `device_sessions`, `proof_credentials`, `interview_sessions` (see migration). Draft shape: `{"answers": {"q1": "...", "q2": "...", "q3": "...", "followup_q": "...", "followup_a": "..."}}`.

## 7. Traceability to the brief

| Brief | Requirement |
|---|---|
| Asks in Tamil or English | FR-6 |
| One follow-up from what they just said | FR-7 |
| Word for word, no summarising | FR-8, FR-14 |
| Reads back; posts only after "yes, post it" | FR-12, FR-13, FR-15 |
| Posts via Proof's connector | §3.2, FR-14 |
| Token only to the user's record, never public, ≤20/day | FR-3, FR-4, FR-15, error matrix |
| Hosted agent + repo; log the build on Proof | Execution_plan Phases 6–7 |
