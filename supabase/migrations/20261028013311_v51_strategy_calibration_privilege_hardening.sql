begin;

revoke all on public.strategy_calibrations from service_role;
revoke all on public.strategy_signal_snapshots from service_role;

grant select, insert on public.strategy_calibrations to service_role;
grant select, insert on public.strategy_signal_snapshots to service_role;

commit;
