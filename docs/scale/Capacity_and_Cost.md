# Capacity, cost and failure drills

All inputs are **assumptions to state out loud** unless marked *(measured)*. The method is the point: write the formula, name the dominant term, and say what you would measure first.

## 1. Load model

Assume a product with 5 M registered students, of which 1 M are active on a given day, and 30% of the active ones record an interview on that day.

| Quantity | Value | Derivation |
|---|---|---|
| Interviews per day | 300 k | 1 M × 30% |
| Voice-minutes per day | 600 k | 300 k × 2 min *(a measured typical run is ~2 min; cap 5 min)* |
| Average concurrent interviews | ~420 | 600 k ÷ 1,440 min |
| Peak concurrent (evening, one time zone) | ~4 k | average × ~10 *(assumption: students cluster after school hours)* |
| Design headroom | ~6 k | peak × 1.5 |
| Posts per day | ≤ 300 k | one per completed interview; at most 20 per user (Proof's limit) |
| API requests per interview | ~12 | session, token status, start, status polls, internal calls (answers, state, preview, post) |
| API requests per second at peak | ~400 rps | 4 k concurrent × 12 requests ÷ 120 s ≈ 0.1 rps per session (UI status polling adds some): **modest** for a few stateless instances |

The API tier is **not** the problem (hundreds of requests per second, stateless). The cost and the risk are in voice minutes, the model provider, and posting to a third party.

## 2. Agent workers

- One agent session per interview. A session is I/O-bound (audio relay + a websocket to the model).
- **Measure first:** sessions per vCPU before audio glitches. Until measured, plan with a conservative **10 sessions per vCPU** *(assumption)*.
- 6 k sessions ÷ 10 = 600 vCPU at peak; with 25% headroom and autoscaling, call it ~150 4-vCPU instances at peak, a handful off-peak. Cold start (tens of seconds today) means scale on a *leading* metric (queue depth / slot usage), not CPU.
- Free tier today: 5 concurrent sessions and 1,000 agent-minutes a month *(measured plan limits)*: about 500 two-minute demos.

## 3. Cost per interview minute (formula, not a quote)

```
cost/min = model_audio_in + model_audio_out + SFU_minutes + agent_compute + egress + ε(db, api, tokens)
```

| Term | How to estimate | Dominant? |
|---|---|---|
| Model audio in/out | tokens/sec of audio × price per token. Audio is on the order of tens of tokens per second *(verify in the provider's docs)*; the model also re-reads accumulated context each turn, so cost grows with length: the 5-minute cap bounds it | **Yes** |
| SFU participant-minutes | 2 participants (student + agent) per interview × provider's per-minute price | second |
| Agent compute | ~0.1 vCPU-class per session *(assumption)* × instance price | small |
| Egress/bandwidth | audio is ~30–60 kbit/s per direction: negligible | no |
| Database / API | rows and requests above are tiny | no |

Worked shape, with **placeholder prices you must replace**: if audio costs `p_a` per minute, SFU `p_s` per participant-minute and compute `p_c` per session-minute, then `cost/interview = 2·(p_a + 2·p_s + p_c)` and the daily bill is that times 300 k. Say the formula, then say: "the first number I would get is `p_a`, because it is more than half of the total, and the second is the fraction of sessions that end early on silence or provider errors, because those cost money and produce nothing."

**Levers, in order of effect:** (1) end dead sessions sooner (silence timer, built); (2) cap length (5-minute hard cap, built); (3) shorter prompts and less context re-read; (4) a cheaper model for the scripted part; (5) pre-warmed pools to avoid paying for idle waiting.

## 4. Database sizing

| Table | Growth | Note |
|---|---|---|
| `interview_sessions` | 300 k rows/day ≈ 110 M/year | ~1 KB each with transcript while in flight; purge drafts at 24 h, keep ids + result URL → ~100 bytes/row steady state ≈ 11 GB/year; partition monthly |
| `device_sessions` / `users` | ≤ 1 M active | indexed by token hash; TTL purge |
| `proof_credentials` | ≤ 1 M rows | ~100 bytes ciphertext + nonce + key id |

A single well-sized Postgres primary handles the write rate (a few hundred rows/second at peak) easily. **Connections** are the real limit (today the shared project allows 5): use a transaction pooler and keep every external call outside a held connection (already a rule in the code).

## 5. Proof as an external dependency

- 20 posts/day/token is per user, so it does not cap the product; **Proof's global capacity and availability** are unknown, so design for them: per-token and global token buckets, exponential backoff with jitter, a circuit breaker, and the unknown-outcome reconciliation job.
- Ask Proof (an interview answer shows initiative): is there an idempotency key, a way to read back a user's recent posts, a bulk/partner rate limit?

## 6. Failure drills (what to rehearse)

| Drill | Expected result | What it proves |
|---|---|---|
| Kill the Gemini connection mid-answer | session ends politely, nothing posted, slot released | safe ending |
| Make Proof time out *after* it applied the post | `post_unknown`, no second post, user told honestly | no blind retry |
| Two "yes" at once (two tabs / agent retry) | exactly one post | atomic claim |
| Student speaks over the read-back | read-back repeated or session ended; no consent counted | integrity of consent |
| Kill an API instance during `posting` | row moves to `post_unknown` after the timeout | crash recovery |
| Revoke the Proof token between read-back and "yes" | clear "reconnect token" message, nothing posted | token death path |
| Fill the active-interview cap | polite "busy" in the UI, no LiveKit spend | cost guard |
| Rotate the encryption key | old tokens still decrypt (key id), new ones use the new key | rotation |
| Restore the database from backup | users reconnect tokens if needed; no post is repeated (result URLs retained) | disaster recovery |

Every one of the first six exists today as an automated test or a live run in the repository; the last three are paper drills until there is infrastructure to run them on.

## 7. What to measure first (in order)

1. Sessions per vCPU on the agent (determines fleet size and cost).
2. Provider error rate and read-back verification failure rate per model version (determines whether you can rely on the model at all).
3. Time from end of speech to first audio, by region (determines regional placement).
4. Fraction of sessions that complete vs end on silence/error (determines real cost per *useful* interview).
5. Unknown-outcome rate at Proof (determines whether the reconciliation job is urgent).
