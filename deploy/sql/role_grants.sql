do $$
declare
    owner_role text := current_setting('arthasignal.new_owner');
    t text;
begin
    execute 'grant usage on schema public to arthasignal_research, api_readonly';
    execute 'grant select on all tables in schema public to arthasignal_research, api_readonly';
    execute 'grant usage, select on all sequences in schema public to arthasignal_research';
    foreach t in array array['backtest_variant_trials', 'backtest_holdout_evaluations', 'scorecard_calls', 'scorecard_grades', 'scorecard_models']
    loop
        if to_regclass('public.' || t) is not null then
            execute format('grant insert on public.%I to arthasignal_research', t);
        end if;
    end loop;
    execute format('alter default privileges for role %I in schema public grant select on tables to arthasignal_research, api_readonly', owner_role);
    execute format('alter default privileges for role %I in schema public grant usage, select on sequences to arthasignal_research', owner_role);
end $$;
