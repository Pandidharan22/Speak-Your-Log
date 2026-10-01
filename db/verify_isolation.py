"""Verify the Speak Your Log database setup and its isolation from the rest of the shared project.

Connects as the limited `syl_app` role (DATABASE_URL) and checks, in order:
  POSITIVE  the app CAN do its job inside schema `speakyourlog` (all writes are rolled back);
  NEGATIVE  the app can NOT read or change anything else (auth, public, other schemas).

Run:  .venv/Scripts/python.exe db/verify_isolation.py
Leaves no data behind. Prints no secrets.
"""
import os
import sys
import uuid

import psycopg
from dotenv import load_dotenv
from psycopg import errors

load_dotenv()
url = os.environ.get("DATABASE_URL", "")
if "PROJECT_REF" in url or not url:
    sys.exit("DATABASE_URL is still the placeholder. Fill it in .env first.")

results: list[tuple[bool, str]] = []


def record(ok: bool, label: str) -> None:
    results.append((ok, label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


# Transaction-pooler (port 6543) does not support prepared statements.
conn = psycopg.connect(url, prepare_threshold=None, connect_timeout=10, autocommit=False)

print("Connection")
cur = conn.cursor()
cur.execute("select current_user, current_setting('search_path'), current_setting('statement_timeout')")
user, search_path, timeout = cur.fetchone()
record(user == "syl_app", f"connected as syl_app (got {user!r})")
record(search_path.strip() == "speakyourlog", f"search_path is 'speakyourlog' (got {search_path!r})")
record(timeout == "8s", f"statement_timeout is 8s (got {timeout!r})")
cur.execute("select rolsuper, rolbypassrls, rolcreaterole, rolcreatedb from pg_roles where rolname = current_user")
sup, byp, crr, crd = cur.fetchone()
record(not (sup or byp or crr or crd), "role is not superuser / bypassrls / createrole / createdb")
conn.rollback()

print("\nPositive: the app can do its job (rolled back afterwards)")
try:
    uid, sid = uuid.uuid4(), uuid.uuid4()
    cur.execute("insert into users (id) values (%s)", (uid,))
    cur.execute("insert into device_sessions (token_hash, user_id, expires_at) values (%s, %s, now() + interval '1 day')",
                (os.urandom(32), uid))
    cur.execute("insert into proof_credentials (user_id, ciphertext, nonce, key_id, last4) values (%s, %s, %s, 'v1', 'abcd')",
                (uid, os.urandom(48), os.urandom(12)))
    cur.execute("insert into interview_sessions (id, user_id, room_name) values (%s, %s, %s)",
                (sid, uid, f"verify-{sid}"))
    cur.execute("update interview_sessions set state = 'confirming' where id = %s and state = 'created' returning id", (sid,))
    record(cur.fetchone() is not None, "insert into all 4 tables + atomic state update works (RLS policy for syl_app is active)")
    cur.execute("select count(*) from interview_sessions where user_id = %s", (uid,))
    record(cur.fetchone()[0] == 1, "select works")
finally:
    conn.rollback()

# Constraint checks (each in its own transaction)
for label, sql, params in [
    ("invalid state is rejected by CHECK", "insert into interview_sessions (user_id, room_name, state) select id, 'x', 'bogus' from users limit 1", ()),
]:
    try:
        cur.execute("insert into users (id) values (%s)", (uuid.uuid4(),))
        cur.execute(sql, params)
        record(False, label)
    except errors.CheckViolation:
        record(True, label)
    finally:
        conn.rollback()

print("\nNegative: the app must NOT reach anything outside its schema")


def expect_denied(label: str, sql: str) -> None:
    try:
        cur.execute(sql)
        record(False, f"{label}  (UNEXPECTEDLY ALLOWED)")
    except (errors.InsufficientPrivilege, errors.UndefinedTable, errors.InvalidSchemaName) as e:
        record(True, f"{label}  ({type(e).__name__})")
    except Exception as e:  # noqa: BLE001
        record(False, f"{label}  (unexpected {type(e).__name__}: {str(e)[:80]})")
    finally:
        conn.rollback()


expect_denied("cannot read auth.users", "select 1 from auth.users limit 1")
expect_denied("cannot read storage.objects", "select 1 from storage.objects limit 1")
expect_denied("cannot read vault secrets", "select 1 from vault.decrypted_secrets limit 1")
expect_denied("cannot create objects in public", "create table public.syl_should_fail (id int)")
expect_denied("cannot create objects in its own schema (DDL is admin-only)", "create table speakyourlog.syl_should_fail (id int)")
expect_denied("cannot create a role", "create role syl_should_fail")

# Any ordinary table in a non-system schema, other than ours, that syl_app can read?
cur.execute("""
    select n.nspname || '.' || c.relname
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('r','v','m','p')
      and n.nspname not in ('pg_catalog','information_schema','pg_toast','speakyourlog')
      and has_table_privilege(current_user, c.oid, 'SELECT')
    order by 1 limit 25
""")
readable = [r[0] for r in cur.fetchall()]
conn.rollback()
if readable:
    print(f"  [INFO] relations outside our schema that syl_app can SELECT (review these): {readable}")
record(not any(r.startswith(("public.", "auth.", "storage.", "vault.")) for r in readable),
       "no readable relations in public / auth / storage / vault")

conn.close()
failed = [label for ok, label in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
