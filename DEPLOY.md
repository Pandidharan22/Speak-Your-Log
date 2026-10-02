# Deploying Speak Your Log

Two things run in the cloud: the **API + web app** (one container on Render, free) and the **voice agent** (LiveKit Cloud, free Build plan). This is a one-time setup of about 30 minutes. Steps marked **you** need your accounts. Nothing here asks you to paste a secret into chat, a file in the repository, or a commit.

## 0. Before you start

- [ ] The code you want to ship is on `develop` and CI is green.
- [ ] You have the Supabase `syl_app` pooler URL, a LiveKit Cloud project (Settings → Keys) and a Gemini API key (the same values as in your local `.env`).
- [ ] Generate **three new, different** keys for production (do not reuse the local dev ones). Run this command three times and keep the outputs somewhere private (a password manager), not in the repo:

```bash
python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

You will use them as `TOKEN_ENC_KEY_V1`, `SESSION_HMAC_KEY` and `AGENT_JOB_SECRET`. The API refuses to start if any two are equal.

## 1. Merge to `main` (you)

`main` is production. Open a pull request from `develop` to `main` on GitHub, check that CI is green, and merge it. (Direct pushes to `main` are not used in this project.)

## 2. API + web app on Render (you)

1. Render dashboard → **New → Blueprint** → connect `Pandidharan22/Speak-Your-Log` → branch `main`. Render reads [`render.yaml`](render.yaml) and shows one service, `speak-your-log`.
2. It asks for the values marked `sync: false`:

| Variable | Value |
|---|---|
| `DATABASE_URL` | the `syl_app` pooler URL (same as local) |
| `LIVEKIT_URL` | `wss://<your-project>.livekit.cloud` |
| `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | from LiveKit Cloud → Settings → Keys |
| `TOKEN_ENC_KEY_V1`, `SESSION_HMAC_KEY`, `AGENT_JOB_SECRET` | the three new keys from step 0 |

3. Click **Apply**. The first build takes a few minutes. When it is live, open `https://<name>.onrender.com/healthz`: it must show `{"status":"ok"}`, and `/readyz` must show `{"status":"ready"}` (that one checks the database).

`PUBLIC_BASE_URL` is not needed; the API uses the URL Render provides. If the service fails to start, the log names the offending variable (never its value).

## 3. Voice agent on LiveKit Cloud (you)

The agent needs only the Gemini key; LiveKit's own credentials are injected by the platform. Create a small secrets file **outside the repository** (for example `C:\Users\<you>\agent-secrets.env`) containing exactly one line:

```
GEMINI_API_KEY=<your Gemini key>
```

Then, from the repository root:

```bash
lk cloud auth                       # opens a browser; log in and select your project
cd apps/agent
lk agent create --secrets-file C:\Users\<you>\agent-secrets.env
```

`lk agent create` builds [`apps/agent/Dockerfile`](apps/agent/Dockerfile) in LiveKit's cloud and deploys it (the Build plan allows one agent). It writes a `livekit.toml` in `apps/agent`; that file holds no secrets. Check that it is running:

```bash
lk agent status
lk agent logs
```

To ship a later change: `cd apps/agent && lk agent deploy`. To go back: `lk agent versions`, then `lk agent rollback`.

Delete the secrets file when you are done; the platform keeps the value.

## 4. Keep the API awake (you)

Render's free service sleeps after 15 minutes without traffic, and the first request then takes about a minute. Before a demo, open the site once. To keep it warm, create a free monitor at [cron-job.org](https://cron-job.org) or UptimeRobot that requests `https://<name>.onrender.com/healthz` every 10 minutes. (`/healthz` does not touch the database.)

## 5. The real-voice test (you, with a microphone)

This is the test the build could not do itself: real voices. On the live URL:

1. Connect your Proof token (Proof → Settings → MCP). The page then shows only `••••` and the last four characters.
2. **English:** answer the three questions and the follow-up. Listen to the read-back. Check that what it says matches the text on screen. Say "yes, post it". Open your Proof profile and confirm the post (verb `built`) is exactly your words.
3. **Tamil** and **Tanglish** (mixed): repeat. Pay attention to whether the model keeps your script (Tamil letters stay Tamil letters) and whether the follow-up quotes something you actually said.
4. **Saying no:** answer, then say "no, cancel" at the read-back. Nothing may be posted.
5. **Saying nothing:** stay silent for about a minute. It should nudge once and then end, posting nothing.
6. Posts on Proof are **public** and limited to 20 a day per token; use an entry you are happy to leave there.

Record what you saw (date, language, what worked, what did not) in [`Dev_Journal.md`](Dev_Journal.md). That is the "we read the record, not just the result" evidence. One thing is still unverified at the time of writing: the shape of Proof's *success* reply to `post_log` (the client parses it tolerantly), so look at the first real post's link on the result screen.

## 6. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Page loads but "Start" fails with a busy message | 4 interviews already running (free-tier cap) | wait a minute |
| Agent never speaks | agent not deployed, or `GEMINI_API_KEY` missing | `lk agent status`, `lk agent logs` |
| Agent greets, then goes silent, or `1011` in the agent log | Google's preview Live model had an internal error | try again; fallback model: `lk agent update --secrets GEMINI_LIVE_MODEL=gemini-3.8-live` |
| "Proof did not accept your token" | token revoked or mistyped | reconnect the token on the page |
| `/readyz` shows `unavailable` | Supabase project paused (free projects pause after about a week idle) | resume it in the Supabase dashboard |
| First visit takes about a minute | Render free instance was asleep | step 4 |

Rollbacks: Render → service → **Events → Rollback**; agent → `lk agent rollback`; database → [`db/rollback/0001_init_down.sql`](db/rollback/0001_init_down.sql) (drops only this app's schema and role).

## 7. Security reminders

- Never put a key in the repository, in chat, or in a build log. `.env` stays git-ignored, and CI fails any commit that tracks one.
- The Proof token lives only in the API (encrypted). The agent never sees it; the browser sees it once, when you type it.
- Key rotation is designed for (key ids, [ADR-004](docs/adr/004-token-encryption.md)) but not configurable by environment variable yet: adding a second key needs a one-line code change in `Settings.token_enc_keys`.
