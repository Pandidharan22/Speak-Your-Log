-- Read-only. Paste into the Supabase SQL editor (as postgres) to confirm 0001_init.sql took effect.
-- Expected values are in the comments. Changes nothing.
select jsonb_pretty(jsonb_build_object(
  'schema_exists',            exists (select 1 from pg_namespace where nspname = 'speakyourlog'),                       -- true
  'tables',                   (select jsonb_agg(c.relname order by c.relname)
                                 from pg_class c join pg_namespace n on n.oid = c.relnamespace
                                where n.nspname = 'speakyourlog' and c.relkind = 'r'),                                 -- 4 tables
  'rls_on_for_every_table',   (select bool_and(c.relrowsecurity)
                                 from pg_class c join pg_namespace n on n.oid = c.relnamespace
                                where n.nspname = 'speakyourlog' and c.relkind = 'r'),                                 -- true
  'syl_app_role',             (select jsonb_build_object('can_login', rolcanlogin, 'superuser', rolsuper,
                                      'bypass_rls', rolbypassrls, 'create_role', rolcreaterole, 'conn_limit', rolconnlimit)
                                 from pg_roles where rolname = 'syl_app'),             -- login true, all others false, limit 10
  'anon_or_authenticated_can_use_schema',
                              has_schema_privilege('anon', 'speakyourlog', 'USAGE')
                           or has_schema_privilege('authenticated', 'speakyourlog', 'USAGE'),                          -- false
  'syl_app_can_use_schema',   has_schema_privilege('syl_app', 'speakyourlog', 'USAGE'),                                -- true
  'syl_app_can_read_auth_users', has_table_privilege('syl_app', 'auth.users', 'SELECT'),                               -- false
  'public_tables_syl_app_can_read',
                              (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
                                where n.nspname = 'public' and c.relkind in ('r','v','m','p')
                                  and has_table_privilege('syl_app', c.oid, 'SELECT'))                                 -- 0
));
