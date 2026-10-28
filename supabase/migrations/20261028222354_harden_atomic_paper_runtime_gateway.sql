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
  
