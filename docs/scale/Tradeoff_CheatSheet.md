# Trade-off cheat-sheet

One line per decision: what I chose, what it cost me, what would make me change my mind. Full reasoning is in the linked ADRs and in [Dev_Journal.md](../../Dev_Journal.md).

| Decision | Chosen | The cost I accepted | I would change it when | Record |
|---|---|---|---|---|
| Conversation engine | One realtime model (Gemini Live) | Less control per turn; preview model; provider errors | a deterministic path (STT → LLM → TTS) is fast enough, or the provider stabilises/prices change | ADR-001, ADR-008 |
| Who may post | Backend only, from one atomic `confirming → posting` update | Slightly more plumbing than "agent calls the API" | never; this is the product's promise | ADR-005 |
| Consent detection | Rule-based classifier (English/Tamil/Tanglish), no LLM | Misses unusual phrasings (answers "unclear", asks again) | an evaluated classifier beats the rules on real data without raising false-confirm rate | ADR-005 |
| What is posted | The student's own words, only trimmed | No labels/summaries, answers may be rambling | a *separate, labelled* summary field is wanted, never replacing the verbatim text | ADR-007 |
| Identity | Anonymous device session cookie | No cross-device login; lose cookie = paste token again | cross-device use matters → accounts | ADR-002 |
| Token storage | AES-256-GCM, AAD = user id, key id | Keys in env vars, rotation manual | scale/compliance → envelope encryption with KMS | ADR-004 |
| Database | Shared Supabase project, own schema, limited role | Tiny connection budget; project pauses when idle; no automatic backups | any real traffic → dedicated database | ADR-003 |
| Hosting | Render free (one container: API + UI) | Sleeps after 15 min; one instance | traffic → CDN for the UI, autoscaled API | Architecture §8 |
| Rate limiting | In-process, per IP/user + global caps | Per process; per-IP spoofable behind the proxy | more than one instance → Redis | Architecture §11 |
| Read-back | Model speaks it, verified against its own transcript, repeated once, else end | A model in the one place that must be exact; occasional repeat | deterministic TTS of the stored text | ADR-008 addendum |
| Failure stance | End safely, never post on doubt; ambiguous result never retried | Some good interviews end unposted | a reconciliation path (read-back from Proof) removes the ambiguity | SRS error matrix |
| Testing | Unit + integration + **mutation testing** of every security-relevant module + live probes | Time | never | Dev_Journal (every step records the bugs injected and what survived) |
| Short answers | Not re-prompted live | A one-word answer is accepted as is; the read-back lets the student redo | the model's reply can be controlled per turn | ADR-008 |
| Docs | Lean and written once, kept honest by the journal | Not exhaustive | team grows | Execution_plan.md |

## Answers to have ready

- **"Why not let the model decide when to post?"** Student speech is untrusted input; "post it now" is a prompt injection waiting to happen. Posting is public and irreversible, so the model has no way to do it.
- **"How do you know it read the log?"** I compare the model's own output transcript with the stored text before any "yes" can count (coverage threshold, order-insensitive, partial transcripts tolerated). If it fails, repeat once, then end without posting. A live run caught a 22-word "read-back" with 0.02 coverage.
- **"What was your worst mistake?"** Trusting that "the speech finished" meant "the speech happened". Then my first fix (a visible `[System request]` marker) made the model announce a failure that never happened. Both are in the journal, with the tests that now pin them.
- **"What if Proof times out after applying the post?"** `post_unknown`: never retried blindly, the student is told honestly, and at scale a reconciliation job checks Proof before releasing the interview.
- **"How would you know if it breaks in production?"** Alerts on: any post without a stored confirmation, `post_unknown` backlog age, read-back verification failure rate, provider error rate, completion rate.
- **"What is not verified?"** Real-voice Tamil quality (tested with synthetic speech) and Proof's real success-reply shape; both are in DEPLOY.md's real-voice test.
