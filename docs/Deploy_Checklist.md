# Deploy checklist: Speak Your Log v1.0.0 (first production deploy)

Companion to [DEPLOY.md](../DEPLOY.md) (the how). This is the *verify* list. Tick each item when you have seen it, not when you have done it.

## Pre-deploy

- [ ] CI is green on `develop` (guard, API, agent, web jobs).
- [ ] No uncommitted work; `git log develop..main` is empty (nothing on `main` that `develop` lacks).
- [ ] No database migration in this release: the schema from `db/migrations/0001_init.sql` is already live and verified (`db/verify_isolation.py` 14/14). **Do not run any migration against the shared Supabase project during this deploy.**
- [ ] Three **new** production keys generated (`TOKEN_ENC_KEY_V1`, `SESSION_HMAC_KEY`, `AGENT_JOB_SECRET`), all different, stored in a password manager. Not the local dev keys.
- [ ] `.env` is not tracked (`git ls-files | grep -E '(^|/)\.env'` prints only `.env.example`).
- [ ] Supabase project is not paused (open the dashboard once).
- [ ] Rollback is understood: Render → Events → Rollback; `lk agent rollback`; nothing to roll back in the database.

## Deploy (order matters: API first, then the agent)

- [ ] PR `develop` → `main` merged. Render Blueprint applied with the production values.
- [ ] `GET /healthz` returns `{"status":"ok"}` and `GET /readyz` returns `{"status":"ready"}` on the Render URL.
- [ ] The page loads with no console errors (strict CSP: a blocked resource shows up in the console).
- [ ] `lk agent create --secrets-file …` finished; `lk agent status` shows it running; the secrets file is deleted.
- [ ] Production API URL is reachable from the internet by the agent (it is called at the public URL given in the dispatch).

## Smoke test (≈ 5 minutes, one post)

- [ ] Connect the Proof token: the page then shows only `••••` + last 4.
- [ ] Start an interview: the agent greets within about 10 s (cold start can be longer).
- [ ] Answer all questions; the follow-up quotes something you said.
- [ ] The read-back on screen equals what is spoken; says it will be public.
- [ ] Say "no, cancel" once: nothing posted. Start again, say "yes, post it": exactly one post appears on the Proof profile, with your words unchanged (verb `built`).
- [ ] Look at the result link: this is the first time the real success reply shape of `post_log` is seen. If the link is missing, the post still happened; check the profile and fix the parser.
- [ ] Silent for about a minute: one nudge, then it ends, nothing posted.

## Monitor for 15 minutes

- [ ] Render logs: no `ERROR`, no stack traces, no token fragments (search the logs for the last 4 characters of your token).
- [ ] `lk agent logs`: no repeated `1011` (Google's preview model erroring) and no `speech_did_not_contain_the_expected_text`.
- [ ] Keep-warm monitor created (`/healthz` every 10 minutes).

## Rollback triggers (decide now, not during)

| Trigger | Action |
|---|---|
| A post appears that the student did not confirm, or text differs from what was shown | Stop: disconnect the agent (`lk agent delete` or scale to 0), investigate before anything else. This is the one unrecoverable class of bug. |
| Page blank or API 5xx for more than 5 minutes | Render rollback to the previous deploy |
| Agent greets but cannot hold a conversation (repeated `1011`) | `lk agent update --secrets GEMINI_LIVE_MODEL=gemini-3.8-live`; if still bad, wait and retry (provider issue) |
| Token vault errors (`decrypt` failures) | You changed `TOKEN_ENC_KEY_V1` after tokens were stored: restore the old value; users must reconnect otherwise |

## Post-deploy

- [ ] Live link added to `README.md`.
- [ ] Tag `v1.0.0`; build record posted to Proof only with your explicit approval (draft is prepared in the repo).
- [ ] Note in `Dev_Journal.md` what the real-voice test showed (Tamil, Tanglish, English).
