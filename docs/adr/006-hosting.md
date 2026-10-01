# ADR-006: Hosting — Render (API + UI) and LiveKit Cloud (agent), no card

**Status:** Accepted · **Date:** 2026-10-01 · **Deciders:** Pandidharan

## Context
Free tiers only; the user has no payment card. We need a public HTTPS URL for the UI/API and a place to run a long-lived agent worker.

## Decision
- **One Docker web service on Render free**: FastAPI also serves the prebuilt React app (single origin → first-party cookie, no CORS, one deploy).
- **Agent on LiveKit Cloud** via `lk agent deploy` (free Build plan: 1 deployment, 5 concurrent sessions, 1,000 agent-minutes/month; no credit card).
- Keep-warm ping for Render; "waking up" UI state.

## Options considered

| Option | Assessment |
|---|---|
| Render free web service | No card; sleeps after 15 min (~1 min cold start); WebSocket unreliable while sleeping — irrelevant, since realtime media goes through LiveKit |
| Hugging Face Spaces | New compute Spaces now need a paid plan. Rejected |
| Cloud Run / Oracle Always Free / Fly | Need a card or are no longer free. Rejected |
| Vercel (UI) + Render (API) | Cross-site cookie problems (third-party cookie blocking) → would need a proxy. Rejected for the MVP |
| Self-host the agent worker (VM) | Possible later; LiveKit-hosted removes ops work |

## Consequences
- Easier: one repo, one deploy for web+API, one command for the agent.
- Harder: cold starts after idle; hard monthly cap (~500 two-minute sessions) — fine for reviewers, stated in the README; no staging environment on free tiers (the `develop` branch is tested locally, `main` is production).
- Revisit: paid tiers or self-hosted workers for SaaS scale (see scale design).

## Action items
1. [ ] Dockerfiles (6.1) · [ ] Render service + env vars (6.2) · [ ] `lk agent deploy` (6.3)
