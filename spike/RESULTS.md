# Phase 0 spike — results (2026-10-01)

Evidence behind ADR-001 (pipeline choice). Scripts: `01_smoke.py`, `02_stt_bakeoff.py`, `03_live.py`. Raw outputs and synthetic audio are git-ignored (`spike/out/`).

**Method.** No hand-recorded audio (time budget). Gemini TTS spoke 9 known sentences (3 Tamil, 3 Tamil+English "Tanglish", 3 English); each candidate transcribed them; score = character error rate (CER) against the known text, plus latency. **Caveat:** clean synthetic speech flatters recognisers — this is a floor check ("does Tamil work at all?"), not a real-world accuracy figure. Real-voice validation is the first live end-to-end test.

## 1. Batch STT (upload clip → text)

| Candidate | Tamil CER | Mixed CER* | English CER | Latency / short clip |
|---|---|---|---|---|
| `gemini-3.5-transcribe` | 0.00–0.03 | 0.52–0.57* | 0.01 | 3.8–4.9 s |
| `gemini-3.1-flash-lite` (prompted) | 0.00–0.02 | 0.09–0.52* | 0.00 | 2–11 s (variable) |
| `gemini-3.8-flash` | 0.38 on 1 clip, then 503/429 | – | – | unusable on free tier |

\* Mixed-language CER is dominated by **script choice**, not mishearing: the same words are written in Tamil script ("ஸ்விட்ச்"), Latin ("switch"), or romanised Tamil ("pannumbothu"), and the same model varies run to run.

- Tamil is **not** on `gemini-3.5-transcribe`'s documented language list, yet it transcribed Tamil near-perfectly (auto-detect).
- `verbatim` mode returned output identical to `default` on every Tamil clip.
- **Latency of 3–5 s per utterance rules out a cascaded STT→LLM→TTS pipeline** for natural conversation (≈8–10 s per turn end-to-end).

## 2. Gemini Live API (streaming speech-to-speech, with input transcription)

| Model | First-audio latency | Heard Tamil as | Reply quality |
|---|---|---|---|
| `gemini-3.1-flash-live-preview` | **0.86–1.11 s** | Tamil script (mixed words in Tamil script) | Quotes the student's exact phrase, replies in the student's language/script |
| `gemini-3.8-live` | 1.14–1.57 s | **Romanised** Tamil (Latin letters) | Replies in romanised Tamil — poor for a Tamil-first product |
| `gemini-2.5-flash-native-audio-preview-12-2025` | 6.4–8.6 s | Tamil script, best accuracy (CER 0.02) | Good, but far too slow |

- Live input transcription is slightly less exact than batch transcribe (e.g. heard "மறுபடியும" where "மறுபடி" was spoken). Close, not identical.

## Conclusions

1. **Conversation path:** Gemini Live `gemini-3.1-flash-live-preview` (~1 s turn latency, understands Tamil, naturally asks the quoting follow-up). It is a *preview* model → model id is configuration, with `gemini-3.8-live` as fallback.
2. **Record path (what gets posted):** the student's words must be faithful. Start with the Live input transcription; where time allows, re-transcribe each utterance with `gemini-3.5-transcribe` off the latency-critical path (it ran CER≈0 on pure Tamil/English) and show the final text on screen at read-back. Script for mixed speech is pinned by an explicit instruction, not left to the model.
3. Free-tier flakiness is real (503/429 on `gemini-3.8-flash`): every model id is an env var and every Gemini call needs bounded retries and a graceful spoken fallback.

## Proof connector (from `tools/list`)

- Tools: `post_log`, `list_my_recent_logs`, `capture_session`.
- `post_log` args: `verb` (enum: built, stuck, mistake, thinking, decided, nothing, quiet, changed, flagged, thank, learned, freely, assumed, noticed, ask, wonder, figure_out, interview), `content` (required), `why` (**required when verb = `decided`**), `evidence_url` (optional http(s)).
- Its description requires the user's own words or an approved draft, and says **the log is public on the user's Proof profile** → the read-back must say so before asking for consent.

### Observed error behaviour (probed in step 2.5 with side-effect-free requests)

| Request | Response |
|---|---|
| no token / wrong token | HTTP **401**, JSON-RPC error `-32001` ("Unauthorized"), `WWW-Authenticate: Bearer realm="proof-mcp"` |
| unknown tool / unknown method / missing method | HTTP **200** with JSON-RPC error `-32601` (so the *body*, not the status, carries tool/protocol errors) |
| `GET` instead of `POST` | HTTP 405 |
| `tools/list` with a valid token | HTTP 200, 3 tools |

**Still unobserved:** the shape of a *successful* `post_log` reply (seeing it requires a real, public post). `app/proof.py` therefore parses success tolerantly and treats any reply Proof marks successful as posted, even if no URL can be found. Verify the real shape during the first end-to-end test (Execution_plan 6.4).
