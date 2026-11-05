do $$
begin
  if not exists (
    select 1 from vault.secrets where name = 'v6_shadow_observer_token'
  ) then
    perform vault.create_secret(
      encode(gen_random_bytes(32), 'hex'),
      'v6_shadow_observer_token',
      'TriConsensus V6 shadow action observer authentication'
    );
  end if;
end
$$;

create or replace function public.v6_validate_shadow_observer_token(p_token text)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $fn$
  select coalesce(
    length(p_token) = 64
    and exists (
      select 1
      from vault.decrypted_secrets
      where name = 'v6_shadow_observer_token'
        and decrypted_secret = p_token
    ),
    false
  );
$fn$;

revoke all on function public.v6_validate_shadow_observer_token(text)
from public, anon, authenticated;
grant execute on function public.v6_validate_shadow_observer_token(text)
to service_role;

select cron.schedule(
  'triconsensus-v6-shadow-observer-15m',
  '12,27,42,57 * * * *',
  $cron$
    select net.http_post(
      url := 'https://mhrrbnufkiveynbasyiy.supabase.co/functions/v1/v6-shadow-observer',
      headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'x-v6-observer-token', (
          select decrypted_secret
          from vault.decrypted_secrets
          where name = 'v6_shadow_observer_token'
        )
      ),
      body := jsonb_build_object(
        'trigger', 'pg_cron',
        'requested_at', now()
      ),
      timeout_milliseconds := 15000
    ) as request_id;
  $cron$
);
