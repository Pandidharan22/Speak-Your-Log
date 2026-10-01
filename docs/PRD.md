# PRD — Speak Your Log

**Status:** Accepted · **Date:** 2026-10-01 · **Owner:** Pandidharan · **Source:** Vruksha Consultancy take-home brief

## 1. Problem

Students on **Proof** do real work (circuits, code, robots) but rarely write about it — least of all in English. A log in their own words is what makes a Proof record worth reading, so the blocker is *writing*, not *doing*.

## 2. Solution

A voice agent that talks with a student for about two minutes, in **Tamil or English**, about what they did today, and saves what they said — **word for word** — as a log on their Proof record, but only after they say "yes, post it".

## 3. Users

| User | Need |
|---|---|
| **Student** (primary) | Speak naturally in Tamil, English, or a mix; end up with a log that sounds like them. |
| **Reviewer** (Vruksha, secondary) | Open a link, talk to the agent, and see a real post on their own Proof record. Judge the build log, not just the result. |

## 4. Goals

1. **Faithful**: the posted log is the student's own words — never summarised or rewritten.
2. **Consent-first**: nothing is posted without an explicit spoken "yes, post it" after a read-back.
3. **Interviewer-quality**: three fixed questions, then **one** follow-up that quotes something the student just said.
4. **Zero-friction**: no sign-up form; the Proof token is entered **once per device** and remembered, securely.
5. **Free to run**: only free-tier services.

## 5. Non-goals (MVP)

Editing or deleting posted logs · accounts across devices · storing audio · languages beyond Tamil/English · multiple logs per session · a mobile app · admin UI · analytics.

## 6. User flow

1. Open the link → first visit silently creates a device identity.
2. **Connect Proof**: paste your Proof token once (get it at Proof → Settings → MCP). We validate it, store it encrypted, and show only `••••` + last 4 characters.
3. **Start**: allow the microphone. Agent greets and asks:
   1. "What did you try today?"
   2. "What broke?"
   3. "Why did you choose that?"
4. Agent asks **one** follow-up built from the student's own words, e.g. *"You said you switched to an IR sensor — why that over the ultrasonic one?"*
5. **Read-back**: the agent reads the log aloud; the exact text is also on screen, with a notice that it will be **public on the Proof profile**.
6. Student says **"yes, post it"** → posted → the link to the new log is shown. Anything else (edit / no / silence) never posts.

## 7. Acceptance criteria

| # | Criterion | How we check |
|---|---|---|
| A1 | A full session (3 questions + follow-up + read-back + post) completes in ≤ 3 minutes of speaking | Hosted end-to-end run |
| A2 | Posted text is made only of the student's transcribed words (no added or rewritten words) | Automated check: posted string == join of recorded answers |
| A3 | No post happens without a validated confirmation in state `confirming` | Unit + failure-injection tests |
| A4 | Proof token never appears in browser responses, storage, logs, or LiveKit metadata | Security tests + code review |
| A5 | A returning device does not paste the token again | Manual + test |
| A6 | Works in Tamil, English, and mixed speech | Real-voice run on the hosted URL |
| A7 | Agent turn latency feels conversational (target p50 ≤ 1.5 s to first audio) | Measured in spike (≈1 s) and in the hosted run |
| A8 | Double-click / retry never creates two posts | Failure-injection tests |

## 8. Constraints & assumptions

- Free tiers only: Gemini (AI Studio), LiveKit Cloud Build (1 agent, 5 concurrent sessions, 1,000 agent-minutes/month), Supabase (shared existing project, isolated schema), Render free (sleeps when idle).
- Proof allows 20 posts/day per token; posts are **public** on the profile.
- Gemini free-tier content may be used by Google to improve its products → disclosed to the student before they speak.
- Deadline 2026-10-02 23:59.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Real-voice Tamil accuracy lower than the synthetic-speech spike | Real-voice test before submission; on-screen read-back lets the student catch errors |
| `gemini-3.1-flash-live-preview` is a preview model | Model id is configuration; fallback `gemini-3.8-live` |
| Free-tier rate limits / 503s | Bounded retries; spoken fallback ("let's try again in a moment") |
| Render cold start (~1 min) after idle | Keep-warm ping; "waking up" UI state |
| LiveKit free-plan hard caps | Fine for review volume (~500 two-minute sessions/month); noted in README |

## 10. Success for this assignment

A reviewer opens the link, talks for two minutes, hears their own words read back, says yes, and finds the log on their Proof record — and the repo's record (journal, ADRs, tests) shows the reasoning behind every decision.
