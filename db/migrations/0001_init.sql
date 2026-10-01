-- Speak Your Log — initial schema.
--
-- Run ONCE in the Supabase SQL editor of the shared project (as the default `postgres` user).
-- Properties:
--   * ADDITIVE ONLY: creates one new schema and one new login role. Touches nothing in `public`,
--     `auth`, `storage` or any existing object. Safe to re-run (idempotent).
--   * ISOLATED: schema `speakyourlog` is not exposed through the Supabase REST/GraphQL API
--     (do NOT add it to "Exposed schemas"), and anon/authenticated get no privileges on it.
--   * LEAST PRIVILEGE: the app connects as `syl_app`, which can only use this schema.
--     The project's service_role key is never needed by this app.
--
-- The role is created WITHOUT a password (it cannot log in yet). Set the password with the
-- separate statement in the setup instructions — never commit it.

create schema if not exists speakyourlog;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'syl_app') then
    create role syl_app login nosuperuser nocreatedb nocreaterole noinherit nobypassrls
      connection limit 10;
  end if;
end
$$;

alter role syl_app set search_path = speakyourlog;
alter role syl_app set statement_timeout = '8s';
alter role syl_app set idle_in_transaction_session_timeout = '15s';

-- Lock the schema down, then grant only what the app needs.
revoke all on schema speakyourlog from public;
revoke all on schema speakyourlog from anon, authenticated;
grant usage on schema speakyourlog to syl_app;

-- ---------------------------------------------------------------------------
-- Identity: a "user" is a browser/device. The cookie value is never stored,
-- only its HMAC-SHA256 (keyed with SESSION_HMAC_KEY), so a DB leak cannot be
-- replayed as a login.
-- ---------------------------------------------------------------------------
create table if not exists speakyourlog.users (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

create table if not exists speakyourlog.device_sessions (
  token_hash   bytea primary key,
  user_id      uuid not null references speakyourlog.users(id) on delete cascade,
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null,
  constraint token_hash_len check (octet_length(token_hash) = 32)
);
create index if not exists device_sessions_user_idx on speakyourlog.device_sessions(user_id);
create index if not exists device_sessions_expiry_idx on speakyourlog.device_sessions(expires_at);

-- ---------------------------------------------------------------------------
-- The Proof token vault. `ciphertext` = AES-256-GCM(token), `nonce` = 12 random bytes,
-- `key_id` = which server key encrypted it (rotation). The row is bound to its owner via
-- GCM additional-authenticated-data = user_id, so ciphertext cannot be moved between users.
-- `last4` is the only fragment ever shown in the UI.
-- ---------------------------------------------------------------------------
create table if not exists speakyourlog.proof_credentials (
  user_id      uuid primary key references speakyourlog.users(id) on delete cascade,
  ciphertext   bytea not null,
  nonce        bytea not null,
  key_id       text  not null,
  last4        text  not null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint nonce_len check (octet_length(nonce) = 12),
  constraint last4_len check (char_length(last4) = 4)
);

-- ---------------------------------------------------------------------------
-- One row per interview. `state` is the consent state machine; the post gateway
-- does an atomic  confirming -> posting  UPDATE ... RETURNING, which is what makes
-- double-posts impossible. `draft` holds the student's verbatim answers.
-- ---------------------------------------------------------------------------
create table if not exists speakyourlog.interview_sessions (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references speakyourlog.users(id) on delete cascade,
  room_name    text not null unique,
  state        text not null default 'created',
  draft        jsonb not null default '{}'::jsonb,
  proof_url    text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  posted_at    timestamptz,
  constraint state_valid check (state in
    ('created','interviewing','confirming','posting','posted','post_unknown','cancelled','failed'))
);
create index if not exists interview_sessions_user_idx on speakyourlog.interview_sessions(user_id, created_at desc);
-- Fast daily-limit count (Proof allows 20 posts/day/token).
create index if not exists interview_sessions_posted_idx on speakyourlog.interview_sessions(user_id, posted_at)
  where state = 'posted';

-- ---------------------------------------------------------------------------
-- Row-level security: ON for every table. Only `syl_app` has a policy; anon and
-- authenticated have neither privileges nor policies (double lock).
-- ---------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['users','device_sessions','proof_credentials','interview_sessions'] loop
    execute format('alter table speakyourlog.%I enable row level security', t);
    execute format('drop policy if exists syl_app_all on speakyourlog.%I', t);
    execute format('create policy syl_app_all on speakyourlog.%I for all to syl_app using (true) with check (true)', t);
  end loop;
end
$$;

revoke all on all tables in schema speakyourlog from public;
revoke all on all tables in schema speakyourlog from anon, authenticated;
grant select, insert, update, delete on all tables in schema speakyourlog to syl_app;
