-- READ-ONLY verification of 0001_init.sql. Changes nothing. Run in the Supabase SQL editor.
-- Expect every row to say PASS. "No rows returned" from the migration only means "no error".

select n, check_name, result, detail from (
  select 1 as n, 'schema speakyourlog exists' as check_name,
         case when exists (select 1 from pg_namespace where nspname = 'speakyourlog') then 'PASS' else 'FAIL' end as result,
         '' as detail

  union all select 2, 'all 4 tables exist',
         case when (select count(*) from pg_tables where schemaname = 'speakyourlog'
                    and tablename in ('users','device_sessions','proof_credentials','interview_sessions')) = 4
              then 'PASS' else 'FAIL' end,
         coalesce((select string_agg(tablename, ', ' order by tablename) from pg_tables where schemaname = 'speakyourlog'), 'none')

  union all select 3, 'row-level security enabled on every table',
         case when not exists (select 1 from pg_class c join pg_namespace s on s.oid = c.relnamespace
                               where s.nspname = 'speakyourlog' and c.relkind = 'r' and not c.relrowsecurity)
              then 'PASS' else 'FAIL' end, ''

  union all select 4, 'policy syl_app_all exists on all 4 tables',
         case when (select count(*) from pg_policies where schemaname = 'speakyourlog' and policyname = 'syl_app_all') = 4
              then 'PASS' else 'FAIL' end, ''

  union all select 5, 'role syl_app: can login, NOT superuser/createdb/createrole/bypassrls/inherit',
         case when exists (select 1 from pg_roles where rolname = 'syl_app' and rolcanlogin and not rolsuper
                           and not rolcreatedb and not rolcreaterole and not rolbypassrls and not rolinherit)
              then 'PASS' else 'FAIL' end,
         coalesce((select 'connection limit = ' || rolconnlimit from pg_roles where rolname = 'syl_app'), 'role missing')

  union all select 6, 'role syl_app defaults (search_path, statement_timeout)',
         case when exists (select 1 from pg_roles where rolname = 'syl_app'
                           and 'search_path=speakyourlog' = any (rolconfig)
                           and exists (select 1 from unnest(rolconfig) c where c like 'statement_timeout=%'))
              then 'PASS' else 'FAIL' end, ''

  union all select 7, 'syl_app can use schema + select/insert/update/delete on all 4 tables',
         case when has_schema_privilege('syl_app', 'speakyourlog', 'USAGE')
                   and not exists (
                     select 1 from pg_tables t
                     where t.schemaname = 'speakyourlog'
                       and not (has_table_privilege('syl_app', format('%I.%I', t.schemaname, t.tablename), 'SELECT')
                            and has_table_privilege('syl_app', format('%I.%I', t.schemaname, t.tablename), 'INSERT')
                            and has_table_privilege('syl_app', format('%I.%I', t.schemaname, t.tablename), 'UPDATE')
                            and has_table_privilege('syl_app', format('%I.%I', t.schemaname, t.tablename), 'DELETE')))
              then 'PASS' else 'FAIL' end, ''

  union all select 8, 'anon + authenticated have NO access to the schema or its tables',
         case when not has_schema_privilege('anon', 'speakyourlog', 'USAGE')
                   and not has_schema_privilege('authenticated', 'speakyourlog', 'USAGE')
                   and not exists (
                     select 1 from pg_tables t
                     where t.schemaname = 'speakyourlog'
                       and (has_table_privilege('anon', format('%I.%I', t.schemaname, t.tablename), 'SELECT,INSERT,UPDATE,DELETE')
                         or has_table_privilege('authenticated', format('%I.%I', t.schemaname, t.tablename), 'SELECT,INSERT,UPDATE,DELETE')))
              then 'PASS' else 'FAIL' end, ''

  union all select 9, 'schema is NOT exposed through the Supabase REST API',
         case when coalesce((select array_to_string(rolconfig, ' ') from pg_roles where rolname = 'authenticator'), '')
                   not like '%speakyourlog%'
              then 'PASS' else 'FAIL' end,
         coalesce((select array_to_string(rolconfig, ' ') from pg_roles where rolname = 'authenticator'), 'authenticator config not found')

  union all select 10, 'ISOLATION: syl_app can read 0 tables in your existing public schema',
         case when (select count(*) from pg_tables t where t.schemaname = 'public'
                    and has_table_privilege('syl_app', format('%I.%I', t.schemaname, t.tablename), 'SELECT')) = 0
              then 'PASS' else 'FAIL' end,
         (select count(*) from pg_tables where schemaname = 'public')::text || ' tables in public checked'
) checks
order by n;
