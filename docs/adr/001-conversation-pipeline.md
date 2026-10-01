# ADR-001: Conversation pipeline — Gemini Live vs cascaded STT → LLM → TTS

**Status:** Accepted · **Date:** 2026-10-01 · **Deciders:** Pandidharan
**Evidence:** [spike/RESULTS.md](../../spike/RESULTS.md)

## Context
The agent must hold a natural two-minute spoken conversation in Tamil/English and ask a follow-up that quotes the student. Free tier only. Our first design assumed a cascaded pipeline because it gives control over the transcript. The spike tested that assumption.

## Decision
Use **Gemini Live** (`gemini-3.1-flash-live-preview`) for the conversation, with the model id in configuration and `gemini-3.8-live` as fallback. Transcripts come from the Live API's input transcription; per-utterance batch re-transcription is an optional refinement (see ADR-007).

## Options considered

### A. Cascaded: batch STT → text LLM → TTS
| Dimension | Assessment |
|---|---|
| Latency | 3–5 s per STT call alone → ≈8–10 s per turn (measured) |
| Transcript control | High (dedicated STT, verbatim prompts) |
| Complexity | High (3 services, turn-taking, interruption handling) |
| Free-tier risk | 3 models, more 503/429 exposure (`gemini-3.8-flash` was unusable) |

### B. Gemini Live, speech-to-speech (chosen)
| Dimension | Assessment |
|---|---|
| Latency | 0.9–1.1 s first audio (measured) |
| Transcript control | Medium: input transcription is close but not exact (e.g. an extra suffix on one Tamil word) |
| Complexity | Low–medium; LiveKit has a Gemini Live plugin |
| Free-tier risk | Preview model may change; one dependency instead of three |

### C. Live 2.5 native-audio
Most accurate transcript (CER 0.02) but 6–8.6 s latency — fails the conversational bar.

## Trade-off analysis
Turn latency is the product experience; an 8-second pause reads as a broken agent. Transcript fidelity can be recovered off the critical path, latency cannot. So we spend complexity on the *record path* (ADR-007), not on the *conversation path*.

## Consequences
- Easier: natural turn-taking, follow-ups that quote the student, fewer moving parts.
- Harder: we do not control the model's wording, so state is tracked by deterministic code, and the read-back text is shown on screen so what is posted is always visible.
- Revisit: when the preview model graduates or changes; pricing if we leave the free tier (see scale design).

## Action items
1. [ ] Hello-agent on Live in a LiveKit room (Execution_plan 3.3)
2. [ ] Eval script with synthetic Tamil/Tanglish/English (3.5)
3. [ ] Real-voice validation on the hosted URL (6.4)
