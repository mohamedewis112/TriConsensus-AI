create table if not exists public.v6_shadow_adaptive_control (
  id text primary key check (id = 'adaptive-v2'),
  activated_at timestamptz not null,
  bootstrap_cutoff timestamptz not null,
  rolling_window integer not null check (rolling_window between 20 and 40),
  fallback_window integer not null check (fallback_window between 8 and 40),
  minimum_regime_samples integer not null check (minimum_regime_samples >= 8),
  weight_step numeric not null check (weight_step = 0.25),
  active boolean not null default true,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

insert into public.v6_shadow_adaptive_control(
  id,activated_at,bootstrap_cutoff,rolling_window,fallback_window,
  minimum_regime_samples,weight_step,active,payload
)
values(
  'adaptive-v2',now(),now(),30,20,8,0.25,true,
  jsonb_build_object(
    'purpose','rolling_regime_aware_safe_prior_v2',
    'training_before_activation','bootstrap_only',
    'validation_after_activation_only',true,
    'weights',jsonb_build_array(0,0.25,0.5,0.75,1),
    'maximum_relative_risk',1,
    'opportunity_cost_recovery',true,
    'hysteresis_max_step',0.25,
    'btc_eth_isolated',true,
    'risk_actor_counterfactual_training',false,
    'v51_protocol_changed',false,
    'trading_authority',false
  )
)
on conflict(id) do nothing;

create table if not exists public.v6_shadow_regime_policy_versions (
  id uuid primary key default gen_random_uuid(),
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  regime_key text not null,
  version text not null unique,
  sample_count integer not null check (sample_count >= 0),
  evidence_count integer not null check (evidence_count >= 0),
  wins integer not null check (wins >= 0 and wins <= evidence_count),
  posterior_win_rate numeric not null check (posterior_win_rate between 0 and 1),
  mean_net_return numeric not null,
  avg_win numeric not null check (avg_win >= 0),
  avg_loss numeric not null check (avg_loss >= 0),
  expected_edge numeric not null,
  target_weight numeric not null check (target_weight in (0,0.25,0.5,0.75,1)),
  applied_weight numeric not null check (applied_weight in (0,0.25,0.5,0.75,1)),
  evidence_source text not null check (evidence_source in ('regime','symbol_fallback','symbol_rolling')),
  opportunity_cost_boost boolean not null default false,
  loss_guard boolean not null default false,
  portfolio_trainable boolean not null default true,
  risk_trainable boolean not null default false,
  mappo_ready boolean not null default false,
  payload jsonb not null default '{}'::jsonb,
  checksum text not null check (length(checksum)=64),
  created_at timestamptz not null default now(),
  unique(symbol,regime_key,sample_count)
);

create table if not exists public.v6_shadow_adaptive_scorecards (
  id uuid primary key default gen_random_uuid(),
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  sample_count integer not null check (sample_count >= 1),
  baseline_wins integer not null,
  shadow_wins integer not null,
  mean_baseline_net numeric not null,
  mean_shadow_net numeric not null,
  cumulative_baseline_net numeric not null,
  cumulative_shadow_net numeric not null,
  baseline_profit_factor numeric,
  shadow_profit_factor numeric,
  baseline_max_drawdown numeric not null,
  shadow_max_drawdown numeric not null,
  losses_avoided integer not null,
  winners_missed integer not null,
  avg_exposure numeric not null,
  exposure_efficiency numeric,
  validation_started_at timestamptz not null,
  payload jsonb not null default '{}'::jsonb,
  checksum text not null check (length(checksum)=64),
  created_at timestamptz not null default now(),
  unique(symbol,sample_count)
);

alter table public.v6_shadow_adaptive_control enable row level security;
alter table public.v6_shadow_regime_policy_versions enable row level security;
alter table public.v6_shadow_adaptive_scorecards enable row level security;

revoke all on public.v6_shadow_adaptive_control from public,anon,authenticated;
revoke all on public.v6_shadow_regime_policy_versions from public,anon,authenticated;
revoke all on public.v6_shadow_adaptive_scorecards from public,anon,authenticated;
grant select on public.v6_shadow_adaptive_control to service_role;
grant select on public.v6_shadow_regime_policy_versions to service_role;
grant select on public.v6_shadow_adaptive_scorecards to service_role;

create index if not exists idx_v6_shadow_regime_policy_latest
  on public.v6_shadow_regime_policy_versions(symbol,regime_key,sample_count desc,created_at desc);
create index if not exists idx_v6_shadow_adaptive_scorecard_latest
  on public.v6_shadow_adaptive_scorecards(symbol,sample_count desc);

drop trigger if exists trg_v6_shadow_adaptive_control_immutable on public.v6_shadow_adaptive_control;
create trigger trg_v6_shadow_adaptive_control_immutable
before update or delete on public.v6_shadow_adaptive_control
for each row execute function private.reject_v6_shadow_learning_mutation();

drop trigger if exists trg_v6_shadow_regime_policy_immutable on public.v6_shadow_regime_policy_versions;
create trigger trg_v6_shadow_regime_policy_immutable
before update or delete on public.v6_shadow_regime_policy_versions
for each row execute function private.reject_v6_shadow_learning_mutation();

drop trigger if exists trg_v6_shadow_adaptive_scorecard_immutable on public.v6_shadow_adaptive_scorecards;
create trigger trg_v6_shadow_adaptive_scorecard_immutable
before update or delete on public.v6_shadow_adaptive_scorecards
for each row execute function private.reject_v6_shadow_learning_mutation();

create or replace function private.v6_shadow_regime_key(p_state jsonb)
returns text
language sql
immutable
security invoker
set search_path = pg_catalog, public, private
as $$
  select
    coalesce(nullif(split_part(p_state #>> '{volatility_state,market_regime}','|',1),''),'unknown')
    || '|' ||
    case
      when nullif(p_state #>> '{volatility_state,realized_volatility}','')::numeric is null
        or nullif(p_state #>> '{volatility_state,realized_volatility}','')::numeric <= 0
        or nullif(p_state #>> '{volatility_state,implied_volatility}','')::numeric is null
        then 'unknown_iv'
      when (p_state #>> '{volatility_state,implied_volatility}')::numeric
        / nullif((p_state #>> '{volatility_state,realized_volatility}')::numeric,0) >= 1.25
        then 'high_iv'
      when (p_state #>> '{volatility_state,implied_volatility}')::numeric
        / nullif((p_state #>> '{volatility_state,realized_volatility}')::numeric,0) <= 0.80
        then 'low_iv'
      else 'normal_iv'
    end;
$$;

revoke all on function private.v6_shadow_regime_key(jsonb) from public,anon,authenticated;

create or replace function private.update_v6_shadow_regime_safe_priors_v2()
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, private, extensions
as $$
declare
  v_symbol text; v_regime text; v_total integer; v_regime_count integer;
  v_n integer; v_wins integer; v_mean numeric; v_avg_win numeric; v_avg_loss numeric;
  v_p numeric; v_edge numeric; v_target numeric; v_prev numeric; v_applied numeric;
  v_source text; v_wins4 integer; v_sum4 numeric; v_wins6 integer; v_sum6 numeric;
  v_loss3 integer; v_boost boolean; v_loss_guard boolean; v_version text;
  v_payload jsonb; v_inserted integer := 0;
begin
  perform pg_advisory_xact_lock(hashtext('triconsensus_v6_adaptive_prior_v2'));

  foreach v_symbol in array array['BTC/USDT'::text,'ETH/USDT'::text]
  loop
    select count(*)::integer into v_total
    from public.v6_shadow_learning_outcomes where symbol=v_symbol;
    if v_total < 8 then continue; end if;

    select count(*) filter(where baseline_net_return>0)::integer,
           coalesce(sum(baseline_net_return),0)
      into v_wins4,v_sum4
    from (select baseline_net_return from public.v6_shadow_learning_outcomes
          where symbol=v_symbol order by evaluated_at desc limit 4) q4;

    select count(*) filter(where baseline_net_return>0)::integer,
           coalesce(sum(baseline_net_return),0)
      into v_wins6,v_sum6
    from (select baseline_net_return from public.v6_shadow_learning_outcomes
          where symbol=v_symbol order by evaluated_at desc limit 6) q6;

    select count(*) filter(where baseline_net_return<0)::integer into v_loss3
    from (select baseline_net_return from public.v6_shadow_learning_outcomes
          where symbol=v_symbol order by evaluated_at desc limit 3) q3;

    for v_regime in
      select 'all'
      union
      select distinct private.v6_shadow_regime_key(t.state_json)
      from public.v6_shadow_learning_transitions t
      join public.v6_shadow_learning_outcomes o on o.transition_id=t.id
      where t.symbol=v_symbol
    loop
      if exists(select 1 from public.v6_shadow_regime_policy_versions
                where symbol=v_symbol and regime_key=v_regime and sample_count=v_total)
      then continue; end if;

      if v_regime='all' then
        v_source := 'symbol_rolling';
        select count(*)::integer,
               count(*) filter(where baseline_net_return>0)::integer,
               coalesce(avg(baseline_net_return),0),
               coalesce(avg(baseline_net_return) filter(where baseline_net_return>0),0),
               coalesce(abs(avg(baseline_net_return) filter(where baseline_net_return<0)),0)
          into v_n,v_wins,v_mean,v_avg_win,v_avg_loss
        from (select baseline_net_return from public.v6_shadow_learning_outcomes
              where symbol=v_symbol order by evaluated_at desc limit 30) q;
      else
        select count(*)::integer into v_regime_count
        from public.v6_shadow_learning_transitions t
        join public.v6_shadow_learning_outcomes o on o.transition_id=t.id
        where t.symbol=v_symbol and private.v6_shadow_regime_key(t.state_json)=v_regime;

        if v_regime_count >= 8 then
          v_source := 'regime';
          select count(*)::integer,
                 count(*) filter(where baseline_net_return>0)::integer,
                 coalesce(avg(baseline_net_return),0),
                 coalesce(avg(baseline_net_return) filter(where baseline_net_return>0),0),
                 coalesce(abs(avg(baseline_net_return) filter(where baseline_net_return<0)),0)
            into v_n,v_wins,v_mean,v_avg_win,v_avg_loss
          from (
            select o.baseline_net_return
            from public.v6_shadow_learning_transitions t
            join public.v6_shadow_learning_outcomes o on o.transition_id=t.id
            where t.symbol=v_symbol and private.v6_shadow_regime_key(t.state_json)=v_regime
            order by o.evaluated_at desc limit 30
          ) q;
        else
          v_source := 'symbol_fallback';
          select count(*)::integer,
                 count(*) filter(where baseline_net_return>0)::integer,
                 coalesce(avg(baseline_net_return),0),
                 coalesce(avg(baseline_net_return) filter(where baseline_net_return>0),0),
                 coalesce(abs(avg(baseline_net_return) filter(where baseline_net_return<0)),0)
            into v_n,v_wins,v_mean,v_avg_win,v_avg_loss
          from (select baseline_net_return from public.v6_shadow_learning_outcomes
                where symbol=v_symbol order by evaluated_at desc limit 20) q;
        end if;
      end if;

      v_p := (v_wins + 2.0) / (v_n + 4.0);
      v_edge := (v_p*v_avg_win)-((1-v_p)*v_avg_loss);

      if v_edge > 0.0015 and v_p >= 0.48 then v_target := 1;
      elsif v_edge > 0.0005 and v_p >= 0.43 then v_target := 0.75;
      elsif v_edge > -0.0005 and v_p >= 0.38 then v_target := 0.50;
      elsif v_edge > -0.0015 and v_p >= 0.33 then v_target := 0.25;
      else v_target := 0;
      end if;

      v_boost := false;
      if v_wins4 >= 3 and v_sum4 > 0 then v_target := greatest(v_target,0.50); v_boost := true; end if;
      if v_wins6 >= 4 and v_sum6 > 0 then v_target := greatest(v_target,0.75); v_boost := true; end if;

      v_loss_guard := false;
      if v_loss3=3 then v_target := least(v_target,0.25); v_loss_guard := true; end if;

      select applied_weight into v_prev
      from public.v6_shadow_regime_policy_versions
      where symbol=v_symbol and regime_key=v_regime
      order by sample_count desc,created_at desc limit 1;

      v_prev := coalesce(v_prev,0.50);
      if v_target > v_prev then v_applied := least(v_target,v_prev+0.25);
      elsif v_target < v_prev then v_applied := greatest(v_target,v_prev-0.25);
      else v_applied := v_target;
      end if;

      v_version := 'v6-adaptive-v2-' ||
        case v_symbol when 'BTC/USDT' then 'btc' else 'eth' end || '-' ||
        replace(replace(v_regime,'|','-'),'/','-') || '-n' || v_total::text;

      v_payload := jsonb_build_object(
        'symbol',v_symbol,'regime_key',v_regime,'sample_count',v_total,
        'evidence_count',v_n,'evidence_source',v_source,'posterior_win_rate',v_p,
        'mean_net_return',v_mean,'avg_win',v_avg_win,'avg_loss',v_avg_loss,
        'expected_edge',v_edge,'target_weight',v_target,'previous_weight',v_prev,
        'applied_weight',v_applied,'weight_step',0.25,'opportunity_cost_boost',v_boost,
        'loss_guard',v_loss_guard,'rolling_window',30,'minimum_regime_samples',8,
        'only_reduce_base_risk',true,'risk_trainable',false,'mappo_ready',false,
        'trading_authority',false
      );

      insert into public.v6_shadow_regime_policy_versions(
        symbol,regime_key,version,sample_count,evidence_count,wins,
        posterior_win_rate,mean_net_return,avg_win,avg_loss,expected_edge,
        target_weight,applied_weight,evidence_source,opportunity_cost_boost,
        loss_guard,portfolio_trainable,risk_trainable,mappo_ready,payload,checksum
      ) values(
        v_symbol,v_regime,v_version,v_total,v_n,v_wins,v_p,v_mean,v_avg_win,v_avg_loss,
        v_edge,v_target,v_applied,v_source,v_boost,v_loss_guard,true,false,false,v_payload,
        encode(extensions.digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex')
      )
      on conflict(symbol,regime_key,sample_count) do nothing;
      if found then v_inserted := v_inserted+1; end if;
    end loop;
  end loop;
  return v_inserted;
end;
$$;

create or replace function private.update_v6_shadow_adaptive_scorecards_v2()
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, private, extensions
as $$
declare
  v_symbol text; v_activation timestamptz; v_n integer; v_bw integer; v_sw integer;
  v_mb numeric; v_ms numeric; v_cb numeric; v_cs numeric; v_bpf numeric; v_spf numeric;
  v_bdd numeric; v_sdd numeric; v_avoided integer; v_missed integer; v_exposure numeric;
  v_eff numeric; v_payload jsonb; v_inserted integer := 0;
begin
  select activated_at into v_activation
  from public.v6_shadow_adaptive_control where id='adaptive-v2' and active=true;
  if v_activation is null then return 0; end if;

  foreach v_symbol in array array['BTC/USDT'::text,'ETH/USDT'::text]
  loop
    with base as (
      select o.baseline_net_return::numeric br,o.shadow_net_return::numeric sr,
             o.effective_exposure::numeric ex,o.evaluated_at,
             row_number() over(order by o.evaluated_at,o.id) rn
      from public.v6_shadow_learning_transitions t
      join public.v6_shadow_learning_outcomes o on o.transition_id=t.id
      where t.symbol=v_symbol and t.created_at>=v_activation
        and t.portfolio_policy_version='v6-shadow-adaptive-v2'
    ),
    curve as (
      select *,exp(sum(ln(1+br::double precision)) over(order by rn))::numeric beq,
               exp(sum(ln(1+sr::double precision)) over(order by rn))::numeric seq
      from base
    ),
    peaks as (
      select *,max(beq) over(order by rn) bpeak,max(seq) over(order by rn) speak from curve
    )
    select count(*)::integer,
           count(*) filter(where br>0)::integer,
           count(*) filter(where sr>0)::integer,
           coalesce(avg(br),0),coalesce(avg(sr),0),
           coalesce(exp(sum(ln(1+br::double precision)))-1,0)::numeric,
           coalesce(exp(sum(ln(1+sr::double precision)))-1,0)::numeric,
           sum(case when br>0 then br else 0 end)/nullif(abs(sum(case when br<0 then br else 0 end)),0),
           sum(case when sr>0 then sr else 0 end)/nullif(abs(sum(case when sr<0 then sr else 0 end)),0),
           coalesce(max((bpeak-beq)/nullif(bpeak,0)),0),
           coalesce(max((speak-seq)/nullif(speak,0)),0),
           count(*) filter(where br<0 and ex=0)::integer,
           count(*) filter(where br>0 and ex=0)::integer,
           coalesce(avg(ex),0),
           case when coalesce(avg(ex),0)>0 then avg(sr)/avg(ex) else null end
      into v_n,v_bw,v_sw,v_mb,v_ms,v_cb,v_cs,v_bpf,v_spf,v_bdd,v_sdd,
           v_avoided,v_missed,v_exposure,v_eff
    from peaks;

    if v_n<1 then continue; end if;
    if exists(select 1 from public.v6_shadow_adaptive_scorecards
              where symbol=v_symbol and sample_count=v_n) then continue; end if;

    v_payload := jsonb_build_object(
      'symbol',v_symbol,'sample_count',v_n,'baseline_wins',v_bw,'shadow_wins',v_sw,
      'mean_baseline_net',v_mb,'mean_shadow_net',v_ms,
      'cumulative_baseline_net',v_cb,'cumulative_shadow_net',v_cs,
      'baseline_profit_factor',v_bpf,'shadow_profit_factor',v_spf,
      'baseline_max_drawdown',v_bdd,'shadow_max_drawdown',v_sdd,
      'losses_avoided',v_avoided,'winners_missed',v_missed,
      'avg_exposure',v_exposure,'exposure_efficiency',v_eff,
      'validation_started_at',v_activation,'lookahead_used',false,'trading_authority',false
    );

    insert into public.v6_shadow_adaptive_scorecards(
      symbol,sample_count,baseline_wins,shadow_wins,mean_baseline_net,mean_shadow_net,
      cumulative_baseline_net,cumulative_shadow_net,baseline_profit_factor,shadow_profit_factor,
      baseline_max_drawdown,shadow_max_drawdown,losses_avoided,winners_missed,avg_exposure,
      exposure_efficiency,validation_started_at,payload,checksum
    ) values(
      v_symbol,v_n,v_bw,v_sw,v_mb,v_ms,v_cb,v_cs,v_bpf,v_spf,v_bdd,v_sdd,
      v_avoided,v_missed,v_exposure,v_eff,v_activation,v_payload,
      encode(extensions.digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex')
    )
    on conflict(symbol,sample_count) do nothing;
    if found then v_inserted := v_inserted+1; end if;
  end loop;
  return v_inserted;
end;
$$;

revoke all on function private.update_v6_shadow_regime_safe_priors_v2() from public,anon,authenticated;
revoke all on function private.update_v6_shadow_adaptive_scorecards_v2() from public,anon,authenticated;

select cron.schedule(
  'triconsensus_v6_adaptive_prior_v2',
  '7,22,37,52 * * * *',
  'select private.update_v6_shadow_regime_safe_priors_v2();'
);
select cron.schedule(
  'triconsensus_v6_adaptive_scorecard_v2',
  '8,23,38,53 * * * *',
  'select private.update_v6_shadow_adaptive_scorecards_v2();'
);
