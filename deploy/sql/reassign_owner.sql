do $$
declare
    r record;
    new_owner text := current_setting('arthasignal.new_owner');
begin
    for r in
        select n.nspname from pg_namespace n
        where n.nspname not in ('pg_catalog', 'information_schema') and n.nspname !~ '^pg_'
          and not exists (select 1 from pg_depend d where d.classid = 'pg_namespace'::regclass and d.objid = n.oid and d.deptype = 'e')
    loop
        execute format('alter schema %I owner to %I', r.nspname, new_owner);
    end loop;
    for r in
        select n.nspname, c.relname, c.relkind from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname not in ('pg_catalog', 'information_schema') and n.nspname !~ '^pg_'
          and c.relkind in ('r', 'p', 'v', 'm', 'S', 'f')
          and not exists (select 1 from pg_depend d where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype in ('e', 'a', 'i'))
    loop
        execute format('alter %s %I.%I owner to %I',
            case r.relkind when 'v' then 'view' when 'm' then 'materialized view' when 'S' then 'sequence' when 'f' then 'foreign table' else 'table' end,
            r.nspname, r.relname, new_owner);
    end loop;
    for r in
        select p.oid::regprocedure::text as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname not in ('pg_catalog', 'information_schema') and n.nspname !~ '^pg_'
          and not exists (select 1 from pg_depend d where d.classid = 'pg_proc'::regclass and d.objid = p.oid and d.deptype = 'e')
    loop
        execute format('alter routine %s owner to %I', r.sig, new_owner);
    end loop;
    for r in
        select format('%I.%I', n.nspname, t.typname) as name from pg_type t join pg_namespace n on n.oid = t.typnamespace
        where n.nspname not in ('pg_catalog', 'information_schema') and n.nspname !~ '^pg_'
          and t.typtype in ('e', 'd') and not exists (select 1 from pg_depend d where d.classid = 'pg_type'::regclass and d.objid = t.oid and d.deptype = 'e')
    loop
        execute format('alter type %s owner to %I', r.name, new_owner);
    end loop;
end $$;
