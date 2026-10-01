# ADR-007: Verbatim record policy — what "in their own words" means in code

**Status:** Accepted · **Date:** 2026-10-01 · **Deciders:** Pandidharan

## Context
The log "must be the person's own words". Speech recognition is imperfect, mixed Tamil/English can be written in several scripts (the same model produced Tamil script and romanised Tamil on similar inputs), and the Live model could paraphrase if allowed to.

## Decision
1. **The log text is only transcribed speech.** `content = Q1 answer + " " + Q2 answer`, `why = Q3 answer + " " + follow-up answer`, each trimmed. No labels, no LLM edits, no translation, no grammar fixes. A unit test asserts the payload equals the joined stored answers.
2. **Script is pinned by instruction**: Tamil words in Tamil script, English words in Latin letters.
3. **Fidelity ladder**: start with Live input transcription; if time allows, re-transcribe each utterance with `gemini-3.5-transcribe` off the latency path and use that text (it scored CER ≈0 on pure Tamil/English).
4. **The student is the final check**: the exact text is on screen at read-back; they can say "no" and redo. Inaccuracy of recognition is therefore visible, not hidden.
5. **No audio is stored**, so fidelity cannot be improved after the session; text drafts are purged within 24 h.

## Options considered

| Option | Assessment |
|---|---|
| LLM "cleans up" the transcript | Violates the brief. Rejected |
| Live transcription only | Fast, slightly inexact |
| Live + batch re-transcription | Higher fidelity, extra calls and complexity — optional (Execution_plan 3.9, cuttable) |
| Store audio for later re-transcription | Better fidelity, worse privacy and storage; not needed for MVP |

## Consequences
- Easier: a crisp, testable definition of "verbatim" and a defensible answer to the reviewer.
- Harder: recognition errors become part of the log unless the student notices; mixed-script output may look unusual.
- Revisit: add a spoken or on-screen "fix this word" flow if real-voice testing shows frequent errors.

## Action items
1. [ ] Payload builder + test (SRS FR-14) · [ ] Script-pinning instruction + eval (3.4/3.5) · [ ] Real-voice check (6.4)
