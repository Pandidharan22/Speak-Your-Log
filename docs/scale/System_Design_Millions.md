# Speak Your Log at "millions of users": system design

Interview-prep material. It starts from what is **actually built** (a free-tier slice: one API instance, one agent deployment, one shared Postgres) and says, tier by tier, what changes and why. Numbers marked *(assumption)* are mine to defend, not measured facts; numbers marked *(measured)* come from this project's spike or live runs. Capacity and cost math: [Capacity_and_Cost.md](Capacity_and_Cost.md). Decision summary: [Tradeoff_CheatSheet.md](Tradeoff_CheatSheet.md).

## 1. Requirements

**Functional**
- A student has a ~2 minute voice conversation (Tamil, English or mixed); the system records their words **verbatim**, reads them back, and posts to the student's Proof record **only after** an explicit spoken yes.
- Per-user secret (the Proof token) stored so that a database leak alone is not a breach.
- A visible result: what was posted, or an honest "I cannot be sure".

**Non-functional (targets for the scaled product)**
| Property | Target | Why it is hard |
|---|---|---|
| Turn latency | p95 < 2.5 s from end of speech to start of reply *(measured today: ~2.3 s, mostly the model's end-of-turn wait)* | Realtime audio; every hop counts |
| Consent integrity | **0** posts without a heard, confirmed read-back | Public posts on a person's profile; unrecoverable |
| Verbatim integrity | posted text == stored transcript == shown text, byte for byte | The product's promise |
| Availability | 99.9% for starting an interview; a failed session must end *safely*, never post | Provider (Gemini/LiveKit/Proof) failures are normal |
| Privacy | no audio retained; token never in logs/browser | Minors possible; regional rules (DPDP in India, GDPR) |
| Cost | bounded per interview minute (see capacity doc) | Voice-model minutes dominate everything |

**Constraints that shape everything:** Proof allows **20 posts/day/token** and has no idempotency key; Gemini Live is a **preview** model with provider-side errors; audio is the cost and latency centre.

## 2. The shape that survives scale

```
                     ┌──────────────── global edge (CDN, WAF, TLS) ───────────────┐
 Browser (React) ────┤  static app            /api/*  ──▶  API fleet (stateless)  │
   │  │              └────────────────────────────────────┬───────────────────────┘
   │  └─ WebRTC audio ──▶ LiveKit SFU (regional) ◀──────── │ dispatch (explicit, job token)
   │                              │                        ▼
   │                              └──────────▶  Agent worker pool (per region) ──▶ Gemini Live
   │                                                │  internal API (job token)
   ▼                                                ▼
 session cookie ─▶ API ──▶ Postgres (primary + replicas, partitioned) ──▶ outbox ──▶ Poster fleet ──▶ Proof
                      └──▶ Redis (rate limits, concurrency slots, locks)        └─ KMS (wraps token DEKs)
```

Principle that does not change with scale: **the agent proposes, the backend disposes.** The agent is an untrusted, replaceable voice; only the backend can post, and only from one atomic state transition.

## 3. What changes, tier by tier

### 3.1 Edge and API (stateless)
- Today: one FastAPI container serving the UI and API. At scale: UI on a CDN; API containers behind a load balancer, autoscaled on CPU and in-flight requests. Nothing is sticky: identity is a cookie → DB/Redis lookup.
- **Rate limits move to Redis** (today per process, per-IP, spoofable behind a proxy; see Architecture §11). Real client IP from the edge's signed header, plus per-user and global token buckets.
- A **concurrency-slot** service (Redis `INCR`/TTL or a semaphore) caps simultaneous interviews per user, per region, and globally; that is the real protection of the voice budget.

### 3.2 Identity
- Device-session cookie stays as the *anonymous* tier (zero friction). Add real accounts (OAuth/magic link; ADR-002 documents the upgrade path) so a Proof token follows the person across devices, with account recovery and deletion.
- Sessions: HMAC-hashed opaque tokens in Postgres with a Redis cache; rotate on privilege change; absolute + idle expiry.

### 3.3 Realtime voice
- LiveKit scales horizontally by SFU nodes; **regional** deployments (India, EU, US) so audio stays near the student: RTT is a direct component of the 2.3 s.
- Agents are **stateful per session but disposable**: a worker pool per region, each worker handling N sessions *(assumption: I/O-bound, tens of sessions per vCPU-class instance; measure before sizing)*. Autoscale on active sessions, with headroom for cold start (today: tens of seconds).
- **Provider failure is a normal state.** Today `1011` errors end a session harmlessly. At scale: retry the model connection once mid-session (context replay), fall back to a second model/provider for new sessions, and circuit-break by error rate.
- Preview model risk: pin versions, canary every provider change on synthetic and recorded conversations before rollout (an evals gate; the project's synthetic-speech probe is the seed).

### 3.4 Interview state and the post gateway
- Interview rows are the source of truth; the agent holds only transient conversation state. A worker crash mid-interview ends it safely (a draft is never posted without a fresh, heard read-back).
- **Posting becomes an outbox + worker fleet.** The API (or gateway) writes `confirming → posting` plus an outbox row in **one transaction**; poster workers consume it. Reasons: (1) Proof's rate limit and outages must not block interactive requests; (2) per-token and global throttles belong in one place; (3) retries must be controlled.
- **Idempotency without Proof's help:** Proof has no idempotency key, so the safe rule today (ambiguous result → `post_unknown`, never retry blind) stays. At scale add a *reconciliation job*: for `post_unknown`, query the user's Proof record (if the API allows reads) for the exact content/time, then resolve to `posted` or release back to `confirming`. If Proof offers an idempotency key, use it and the ambiguity class mostly disappears.
- **Daily limit (20/day/token):** counted in our DB before calling; per-token queue so one heavy user cannot starve the shared Proof capacity.

### 3.5 Data
- Hot tables: `interview_sessions` (≈ one row per interview). Partition by month; drafts hold transcript text only while an interview is in flight (purged after 24 h, already built); `posted` rows keep ids and the result URL, not the text, unless the user opts in to history.
- Postgres primary + read replicas; PgBouncer/transaction pooling (already assumed). Shard by `user_id` hash only when a single primary is the proven bottleneck (it will not be for a long time: see capacity doc).
- Multi-region: **home region per user** (data residency), writes go to the home region; cross-region reads are rare. Not active-active; the added consistency cost buys nothing here.
- No audio stored. Transcripts are personal data: retention, export and deletion APIs; deletion cascades through drafts, credentials, sessions.

### 3.6 Secrets
- Envelope encryption: a **per-user data key** wraps the token (AES-256-GCM, AAD = user id, as built), and the data key is wrapped by a **KMS-held key**. Rotation = rewrap data keys, no re-encryption of tokens; revocation = destroy a key. The vault's key-id field already supports this.
- Only the poster fleet may call KMS decrypt; the agent and the browser never can. Decrypt in memory, for the call only.

### 3.7 Observability and safety nets
- Per-turn metrics (latency by hop), session outcome counts (completed / cancelled / silent / provider-error / unheard-readback), post outcomes (posted / retryable / rejected / unknown), the **unknown backlog** and its age.
- Alerts that matter: any post without a recorded confirmation text; `post_unknown` growth; read-back verification failure rate (a regression in the model shows up here first); provider error rate.
- A kill switch for the agent fleet and for posting separately.

## 4. Failure modes and what the system does

| Failure | Behaviour today | At scale |
|---|---|---|
| Gemini session error (`1011`) | session ends, nothing posted | reconnect once with context replay; else polite end; circuit breaker + fallback model |
| Model skips the read-back | verified against its transcript; repeat once; else end, nothing posted | same, plus an alert on the failure rate |
| Student says "yes" while the log is still being read | ignored (arrival step guard) | same |
| Proof timeout / 5xx | `post_unknown`, never retried blindly | reconciliation job; per-token backoff |
| Proof 429 / our daily limit | back to `confirming`, friendly message | queue and tell the user "will post when allowed" only if consent allows deferral (decision: no, keep it synchronous) |
| Double submit / two agents | atomic compare-and-set; one wins | same |
| API instance dies mid-post | stale `posting` → `post_unknown` after 2 min | same; outbox makes it recoverable by design |
| DB primary down | readiness fails; interviews refused | failover to replica; interviews degrade to "try later"; no posting without the DB |
| LiveKit region down | interviews fail to start in that region | route new sessions to the next region (latency cost) |
| Token vault key lost | tokens unreadable → users reconnect | KMS with multi-region keys and backups; rehearse |
| Abuse (fake users, spoofed IPs) | global caps; token must be valid at Proof | WAF, proof-of-work/captcha on session creation, per-token trust scoring |

## 5. What I would revisit as it grows

1. **Read-back via deterministic TTS** instead of asking the model to read: removes the model from the one moment that must be exact (documented as future work in the ADR-008 addendum).
2. **Move off a preview model** or run two providers; build the evals gate first.
3. **Accounts** instead of device sessions once cross-device use matters.
4. **Outbox + poster fleet** when posting volume or Proof outages make synchronous posting painful.
5. **Redis** the moment there is more than one API instance (limits, slots).
6. **Cost controls**: shorter system prompts, a cheaper model for the interview and a stronger one only for hard cases, silence detection that ends dead sessions earlier.
