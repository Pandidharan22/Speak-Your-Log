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
