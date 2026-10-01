-- Removes EVERYTHING created by 0001_init.sql and nothing else. Destroys all Speak Your Log data.
drop schema if exists speakyourlog cascade;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'syl_app') then
    drop owned by syl_app;
    drop role syl_app;
  end if;
end
$$;
