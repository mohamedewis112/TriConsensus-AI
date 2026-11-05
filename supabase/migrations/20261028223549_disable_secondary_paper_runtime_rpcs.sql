revoke execute on function public.triconsensus_open_paper_trade(text,jsonb)
  from service_role, anon, authenticated, public;
revoke execute on function public.triconsensus_monitor_paper_trade(text,jsonb)
  from service_role, anon, authenticated, public;
revoke execute on function public.triconsensus_close_paper_trade(text,jsonb)
  from service_role, anon, authenticated, public;

