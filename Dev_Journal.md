# Dev Journal

One entry per committed step: what was done and why. Newest at the bottom. Entries are committed separately from the work they describe (see CLAUDE.md rule 3).

---

## Step 0 — Repo bootstrap (`c7d4622`, 2026-10-01)

**What:** Initialised the git repo with `main` (production) and `develop` (working branch). Added `CLAUDE.md` (process rules, stack, security invariants, current status) and a strict `.gitignore` (`.env*` ignored, `.env.example` allowed).

**Why:** Fixing the working agreement before any code exists is cheaper than retrofitting it. The `.gitignore` goes in the very first commit so a secret can never be committed "by accident, once". The security invariants (token never reaches the browser, LLM never writes or posts the log) are written down now so every later design decision can be checked against them.

**Research that shaped it:** Verified free-tier limits from official docs. Two findings drive the plan: (1) Tamil speech-to-text support in Gemini is unverified, so a spike comes before any build; (2) Gemini free-tier content may be used to improve Google products, which needs a user-facing disclosure.

---

## Step 1 — Setup, env template, and three design corrections (`2650b1e`, 2026-10-01)

**What:** Added `.env.example` (placeholders only), git-ignored the spike audio folder, and updated `CLAUDE.md` with the decisions below. The user filled a local `.env`; I validated its format (key lengths, base64 decoding, distinctness) without ever printing a value.

**Decisions and why:**
- **No Supabase Auth; device-session cookie instead.** The user's only Supabase project ("Anthaathi") is shared. Supabase's built-in email sender allows ~2 emails/hour to team members only, so magic links would never reach evaluators; fixing it with custom SMTP is a project-wide setting that would alter Anthaathi's emails. Sign-ups would also land in Anthaathi's `auth.users` with the `authenticated` role. A cookie-based device identity has none of these side effects. Cost: per-device, not cross-device. Magic link remains the upgrade path.
- **No `service_role` key.** It bypasses RLS for the whole Anthaathi project and would live in the hosting environment. Instead a dedicated schema + limited role (`syl_app`) confines the blast radius to this app's tables.
- **Spike on the API key alone.** The user has ~10–12 usable hours and limited Claude usage. Tamil STT risk is retained but tested with Gemini-TTS-synthesised audio (known ground truth) instead of hand-recorded clips. Known weakness: clean synthetic audio overstates accuracy, so real-voice validation moves to the first end-to-end test.
- **Time budget:** deadline 2026-10-02 23:59; deploy a working slice early, polish after.

---

## Step 2 — Database schema in the shared Supabase project (`f23bfcc`, 2026-10-01)

**What:** `db/migrations/0001_init.sql` creates schema `speakyourlog` (users, device_sessions, proof_credentials, interview_sessions) and a login role `syl_app` with no privileges anywhere else. Added a rollback script, a read-only admin check (`verify_admin.sql`) and `verify_isolation.py`, which connects as `syl_app` and proves both what it can do and what it is denied.

**Why:** The only Supabase project is shared with another live app (Anthaathi), so isolation is the requirement, not a nicety. Additive-only SQL, a schema not exposed to the REST API, RLS on every table, and a role that cannot touch `auth`/`public`/`storage`/`vault` keep any bug or leak in this app from reaching Anthaathi. Only a hash of the device cookie is stored, and the Proof token is stored only as AES-GCM ciphertext (the key lives outside the DB). The consent state machine is a CHECK-constrained column so an invalid state is impossible, and the `confirming → posting` transition can be a single atomic UPDATE.

**Verification:** admin query matched every expected value; `verify_isolation.py` passed 14/14 against the live database. "Success, no rows returned" only means no error, so the negative tests (denied on auth/storage/vault/public, cannot create roles or tables) are what actually prove isolation. Known note: the role can read two Postgres statistics views in `extensions`; harmless platform views.
