# ADR-004: Proof-token protection — application-level AES-256-GCM

**Status:** Accepted · **Date:** 2026-10-01 · **Deciders:** Pandidharan

## Context
The Proof token lets its holder publish public logs as the student. It must be stored so users do not re-paste it, be decryptable only on the server, and never reach browser JavaScript.

## Decision
Encrypt in FastAPI with **AES-256-GCM**: random 12-byte nonce per encryption, **AAD = user id**, `key_id` stored beside the ciphertext for rotation. Keys come from environment variables (`TOKEN_ENC_KEY_<id>`), never from the DB. Decrypt in memory only at post time. The browser can submit the token once and afterwards only sees `connected` + last 4 characters.

## Options considered

| Option | Assessment |
|---|---|
| **A. Plaintext column + RLS** | Violates the requirement. Rejected |
| **B. `pgcrypto` / DB-side encryption** | Key or passphrase travels in SQL; appears in logs and query stats. Rejected |
| **C. Supabase Vault** | Managed keys, but decryption happens inside the DB and key and ciphertext share one trust domain; granting our role the decrypted view would also expose Anthaathi's secrets in the shared project |
| **D. App-level AES-GCM (chosen)** | Two systems must be breached (DB + API environment); simple, testable, no shared-project coupling |
| **E. Cloud KMS envelope encryption** | The right production answer; needs a cloud account/card now. Documented as the scale path |

## Trade-off analysis
App-level encryption makes us responsible for correct crypto use, so we use a vetted library (`cryptography`), a standard AEAD mode, and tests for the failure cases (tamper, wrong user, wrong key, rotation). The key still sits in a Render env var — acceptable here, and replaced by KMS-wrapped per-user keys at scale.

## Consequences
- Easier: DB backup/leak alone yields nothing usable; rotation = re-encrypt rows with a new `key_id`.
- Harder: losing the key makes stored tokens unrecoverable (users re-paste); dev and prod keys must differ.
- Revisit: KMS/HSM envelope encryption and per-tenant data keys (scale design).

## Action items
1. [ ] `crypto.py` + unit tests (Execution_plan 2.4)
2. [ ] Test that no response/log contains the token (2.6)
