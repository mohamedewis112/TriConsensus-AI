revoke execute on function public.triconsensus_open_paper_trade(text,jsonb)
  from anon, authenticated, public;
revoke execute on function public.triconsensus_monitor_paper_trade(text,jsonb)
  from anon, authenticated, public;
revoke execute on function public.triconsensus_close_paper_trade(text,jsonb)
  from anon, authenticated, public;

grant execute on function public.triconsensus_open_paper_trade(text,jsonb)
  to service_role;
grant execute on function public.triconsensus_monitor_paper_trade(text,jsonb)
  to service_role;
grant execute on function public.triconsensus_close_paper_trade(text,jsonb)
  to service_role;

