# ADR-005: Consent and posting — deterministic gate; the agent proposes, the backend disposes

**Status:** Accepted · **Date:** 2026-10-01 · **Deciders:** Pandidharan

## Context
The brief demands posting only after the student says "yes, post it". The student's speech is untrusted input to an LLM, so "the model decided to post" is not an acceptable control. Posts are public and limited to 20/day per token; the Proof call is not idempotent.

## Decision
1. **State machine in code** (`created → interviewing → confirming → posting → posted | post_unknown | failed | cancelled`), enforced by a DB CHECK and atomic `UPDATE … WHERE state='confirming'`.
2. **The LLM never has a post tool.** A separate intent classification (closed enum: `confirm | edit | cancel | unclear`; rules first, constrained LLM fallback) runs on the student's reply to the read-back.
3. **The backend builds the payload** from the stored draft (FR-14); the agent can only say "confirmed", never supply text.
4. **Ambiguous outcomes are not retried.** A timeout after sending the request sets `post_unknown`; the user is told to check their profile.

## Options considered

| Option | Assessment |
|---|---|
| **A. Expose `post_log` as an LLM tool** | Simplest; one prompt-injection or hallucination away from an unconsented public post. Rejected |
| **B. Agent calls Proof directly** | Agent would need the decrypted token → widens the secret's reach. Rejected |
| **C. Agent signals confirmation; backend verifies state and posts (chosen)** | Secrets stay in one process; guard is one SQL statement |
| **D. On-screen button only** | Safer still, but the brief specifies a spoken "yes, post it" |

## Trade-off analysis
We accept more plumbing (internal endpoints, signed job token) for a posting path where the only way to publish is `confirming` + a classified `confirm`. The residual risk is a compromised agent process; it is bounded by interview-scoped tokens, the stored draft being the only payload, and the daily guard.

## Consequences
- Easier: exhaustive tests of every transition; clear audit trail (confirmation text stored in the draft).
- Harder: classifier must handle Tamil/Tanglish/English yes-variants and negations ("yes, but change…" must not confirm).
- Revisit: a visible "Post" button as a second factor if reviewers prefer.

## Action items
1. [ ] State machine tests (3.1) · [ ] Consent classifier + eval set (3.6)
2. [ ] Post gateway + failure-injection tests (4.1, 4.2)
