# ADR-003: Database — isolated schema and limited role in the shared Supabase project

**Status:** Accepted · **Date:** 2026-10-01 · **Deciders:** Pandidharan

## Context
Supabase's free tier allows two projects and both are used; one ("Anthaathi") is live. We need Postgres without disturbing it.

## Decision
Create schema `speakyourlog` and login role `syl_app` via an additive, idempotent migration. The API connects **directly through the Supavisor transaction pooler** as `syl_app`. The schema is not exposed to the REST API; every table has RLS enabled with a policy only for `syl_app`; `anon`/`authenticated` have no privileges. We never receive or store the project's `service_role` key.

## Options considered

| Option | Assessment |
|---|---|
| **A. Use `public` schema + `service_role` via supabase-py** | Easiest, but `service_role` bypasses RLS for *all* Anthaathi data and would live in our hosting env; table-name collisions possible. Rejected |
| **B. New Supabase project** | Cleanest isolation, impossible (free limit reached) |
| **C. Other free Postgres (Neon, etc.)** | Viable, but adds a vendor and the user already has Supabase. Kept as fallback |
| **D. Own schema + limited role + direct pooler connection (chosen)** | Blast radius = our four tables; reversible with one script |

## Trade-off analysis
We give up Supabase's REST/realtime conveniences, which we do not need (a single trusted backend does all access). In exchange the worst-case compromise of this app reaches only its own data, and Anthaathi's configuration is untouched.

## Consequences
- Easier: provable isolation (`db/verify_isolation.py`, 14 checks passed against the live database); one-script rollback.
- Harder: schema changes need an admin to run SQL (the app role has no DDL); transaction-pooler mode needs prepared statements disabled.
- Shared-resource caveats: connection count and the 500 MB cap are shared with Anthaathi; role limited to 10 connections; free projects pause after a week idle (Anthaathi's activity keeps it awake).
- Authorization lives in the API (every query scoped by `user_id`); RLS is defence in depth, not the primary control.

## Action items
1. [x] Migration, rollback, verification (Execution_plan 0.3)
2. [ ] Keep pool ≤3 connections (2.2)
