do $$
    declare
      r record;
      ddl text;
    begin
      for r in
        select p.oid
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
            'triconsensus_runtime_snapshot',
            'triconsensus_runtime_commit_decision',
            'triconsensus_runtime_monitor_paper_trade'
          )
      loop
        ddl := pg_get_functiondef(r.oid);
        ddl := replace(
          ddl,
          'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933',
          'e0fd12769e72b1e3405e2653b8ffeb916d7d87ab3a87454cb3d27baedae28209'
        );
        execute ddl;
      end loop;
    end
    $$;

    revoke all on function public.triconsensus_runtime_snapshot(text)
      from public, anon, authenticated;
    revoke all on function public.triconsensus_runtime_commit_decision(text,jsonb)
      from public, anon, authenticated;
    revoke all on function public.triconsensus_runtime_monitor_paper_trade(text,jsonb)
      from public, anon, authenticated;

    grant execute on function public.triconsensus_runtime_snapshot(text)
      to service_role;
    grant execute on function public.triconsensus_runtime_commit_decision(text,jsonb)
      to service_role;
    grant execute on function public.triconsensus_runtime_monitor_paper_trade(text,jsonb)
      to service_role;
  
