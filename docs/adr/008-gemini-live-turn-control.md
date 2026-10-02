# ADR-008: Controlling a conversation with Gemini Live — pre-armed instructions, not in-the-moment steering

**Status:** Accepted · **Date:** 2026-10-02 · **Deciders:** Pandidharan
**Evidence:** step 3.4 timeline measurements (below); LiveKit docs on turn handling.

## Context
The interview must follow a script (Q1 → Q2 → Q3 → one follow-up → read-back → confirm) and the consent logic must stay in deterministic code (ADR-005). The plan assumed the state machine would decide each reply *after* hearing the student. With Gemini Live that assumption is false.

**Measured, one turn, wall-clock aligned between the student simulator and the worker:**

| Moment | Time |
|---|---|
| Student finishes speaking | 548.75 s |
| Agent's reply **audio starts** | 551.19 s (+2.4 s) |
| Student's **final transcript** reaches our code | 555.24 s (+6.5 s, after the whole reply was spoken) |

Gemini Live decides end-of-turn itself and replies immediately; LiveKit's docs state it "does not support client-side turn-taking". The transcript only arrives after the reply is finished, so there is **no window** in which our code can look at the answer and then choose what the model says.

## Decision
1. **Pre-arm the model.** While the student answers question N, its instructions already say what to do when they stop (`interview/script.py`): acknowledge briefly and ask exactly question N+1; after Q3, ask **one** follow-up that quotes a phrase the student said.
2. **Neutral filler at branching moments.** Where the next move depends on what the student said or on server state (after the follow-up answer, at read-back, at confirmation), the pre-armed reply is only "Okay." The **driver then speaks explicitly** with `generate_reply`: the exact read-back text from the API, the consent question, the outcome.
3. **The state machine records and re-arms, it does not steer.** After each finished answer the driver stores the words verbatim and sets the instructions for the turn that is starting.
4. **No live "re-prompt a short answer".** It would require a decision before the reply (SRS FR-9, a SHOULD). The student still sees and can redo their words at the read-back (FR-12/FR-17).
5. **Instructions are set when the agent is created, never just before the first utterance.** Updating instructions makes this model restart its connection; doing it right before the greeting lost the greeting (observed: `generate_reply timed out`).

## Options considered

| Option | Assessment |
|---|---|
| A. Client-side manual turn control with Gemini Live | Not supported (LiveKit docs); the model keeps its own turn-taking |
| B. Disable Gemini's turn detection, use LiveKit VAD + our own commit | Possible in principle, but needs extra models, plugin wiring we do not control, and risks cutting off students who pause to think |
| C. Cascaded STT → LLM → TTS (full control) | Rejected in ADR-001: 8–10 s per turn |
| **D. Pre-arming + explicit speech at branches (chosen)** | Works with the model's native behaviour; keeps ~1 s-class replies; control is "soft" before the transcript and "hard" after |

## Trade-off analysis
We give up hard control of *what the model says in the moment* and keep hard control of *what is recorded and what is posted*. Consent and the post gateway never depended on the model's wording: they depend on the transcript classified by deterministic code and on a server-side state change, so the weaker steering does not weaken the guarantees.

## Consequences
- The model can drift (ask a different question, or two). Mitigations: narrow per-step instructions, tests on the instruction text, a small evaluation set (step 3.5), and the read-back showing exactly what will be posted.
- Language matching is good but not exact: with Tamil speech the agent switched to Tamil for Q3 and the follow-up, while Q2 stayed in English.
- Reply latency is about 2.3 s from end of speech, dominated by Gemini's end-of-turn silence wait; can be tuned later, with the risk of cutting off students who pause.
- Instruction updates restart the model connection, so they happen only between turns.

## Action items
1. [x] `script.py` (pre-armed instructions), `driver.py` (re-arm + explicit speech), tested with a fake voice (3.4)
2. [ ] Follow-up quality eval set (3.5) · [ ] Consent classification and explicit read-back/confirm speech (3.6, 3.7)
3. [ ] Consider tuning Gemini's end-of-speech silence for latency (polish)


## Addendum (2026-10-02): what the first full live runs taught us

Measured against the real LiveKit + Gemini Live stack with a fake Proof endpoint (nothing was posted to the real service). These findings change the rules for any code that makes the model speak.

1. **An explicit request made while the model is generating is silently dropped.** After every student turn the model produces its own short reply. Our transcript arrives while that is still being produced; a `generate_reply` made in that window is paired by the framework with the *filler's* generation and reported complete, though nothing was said. Rule: wait until the model has been quiet before any explicit speech (`LiveKitVoice._wait_until_idle`).
2. **"The speech finished" is not evidence the log was read.** The read-back is verified against the model's own output transcript (`interview/verify.py`: the log's words must appear). Unverified read-backs are repeated once, then the session ends without posting. In one live run the model spoke 22 words with 0.02 coverage; the net caught it and nothing could be approved.
3. **Instructions go in as user-role messages, not as `instructions=`.** The Google plugin sends `generate_reply(instructions=...)` as a *model-role* turn, so the model treats the text as already said and continues after it (it skipped the passages, or spoke a fragment of the instruction itself). Plain user-role requests fix this. Their echo in the conversation is recognised by exact text (`OwnRequests`) and never treated as student speech.
4. **Do not mark requests with a visible convention.** A `[System request ...]` prefix made the model imitate it aloud and invent "the system failed to post the log". No marker; exact-match recognition instead.
5. **The automatic reply to the consent turn can guess an outcome.** The pre-armed neutral reply now prescribes one literal word and states that the model does not know the result. This reduced, but cannot eliminate, the model's tendency to comment; the outcome is announced by the system's explicit request afterwards, so the student may hear it twice (cosmetic; accurate in all observed runs). Everything that matters is decided in code.
6. **Interruptions cannot be disabled** with server-side turn detection, so `speak()` reports `interrupted` and the driver treats it like an unheard read-back.
7. **Provider flakiness is real.** The preview Live model returned `1011 Internal error` for several consecutive sessions, while a bare session with the same audio and instructions worked: the failure is inside long, multi-turn sessions on the provider side. The fallback model (`gemini-3.8-live`) works but voiced its own reasoning aloud, so it is a last resort. The silence timer and clean shutdown turned these failures into harmless "nothing was posted" endings.

**Future work, not done:** reading the log back with a separate TTS call (deterministic text-to-speech of exactly the stored text) would remove the model from the read-back entirely; it was left out because it needs a second audio path into the room.
