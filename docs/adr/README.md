# Architecture Decision Records

Format: Context → Decision → Options → Trade-offs → Consequences → Actions. One decision per file; superseded decisions stay, marked `Superseded`.

| # | Decision | Status |
|---|---|---|
| [001](001-conversation-pipeline.md) | Gemini Live for the conversation, not cascaded STT→LLM→TTS | Accepted |
| [002](002-device-session-identity.md) | Device-session cookie identity, not Supabase Auth / magic link | Accepted |
| [003](003-database-isolation.md) | Own schema + limited role in the shared Supabase project | Accepted |
| [004](004-token-encryption.md) | App-level AES-256-GCM for the Proof token | Accepted |
| [005](005-consent-and-post-gateway.md) | Deterministic consent gate; backend posts, agent only proposes | Accepted |
| [006](006-hosting.md) | Render (API + UI) and LiveKit Cloud (agent), no card | Accepted |
| [007](007-verbatim-record-policy.md) | What "in their own words" means in code | Accepted |
