# ADR-002: Identity — device-session cookie instead of Supabase Auth

**Status:** Accepted · **Date:** 2026-10-01 · **Deciders:** Pandidharan

## Context
The Proof token must be remembered between sessions, so the system needs to recognise a returning user. Constraints: no card, one day, and the only Supabase project is **shared with a live app (Anthaathi)**. The user initially preferred magic links.

## Decision
Issue an anonymous **device identity**: an opaque random cookie (httpOnly, Secure, SameSite=Lax) mapped server-side to a `users` row; only its HMAC-SHA256 is stored. No emails, no passwords, no Supabase Auth.

## Options considered

### A. Supabase Auth magic link
| Dimension | Assessment |
|---|---|
| Complexity | Low code, but project-wide config |
| Delivery | Built-in sender: ~2 emails/hour and only to team members → evaluators would never receive a link. Custom SMTP fixes it but is a project-wide setting that changes Anthaathi's emails |
| Isolation | Users land in Anthaathi's `auth.users` with the `authenticated` role; any loose Anthaathi policy or signup trigger now applies to our users |

### B. Email + password in our own table
Rejected: hand-rolled hashing, verification, reset, and brute-force protection; collects PII we do not need.

### C. Device-session cookie (chosen)
| Dimension | Assessment |
|---|---|
| Complexity | Low (one table, one dependency) |
| Friction | Lowest — reviewers paste a token and talk |
| Isolation | Zero impact on Anthaathi |
| Limitation | Per-device; clearing cookies means re-pasting the token |

## Trade-off analysis
The cookie is as strong a *session* as Supabase Auth's browser session; what it lacks is email-verified identity and cross-device recovery, neither of which this assignment needs. Choosing it removes three risks to the neighbouring app and the email-delivery problem.

## Consequences
- Easier: no email infra, minimal PII, trivial reviewer onboarding.
- Harder: no cross-device login; a stolen cookie lets someone start an interview as that user (they still cannot read the token and still need spoken consent to post).
- Revisit: add Google/magic-link login post-deadline, ideally in a dedicated Supabase project or an external IdP. Identity is a small interface (`current_user()` dependency) so swapping is local.

## Action items
1. [ ] Cookie issue/verify + hash storage + Origin check (Execution_plan 2.3)
2. [ ] Revocation path via "Disconnect" (2.6)
