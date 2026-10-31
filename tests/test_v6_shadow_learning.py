from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20261029012009_add_v6_parallel_shadow_learning.sql"
)
ADAPTIVE_MIGRATION = Path(
    "supabase/migrations/20261030180119_add_v6_adaptive_regime_safe_prior_v2.sql"
)
OBSERVER = Path("supabase/functions/v6-shadow-observer/index.ts")


def test_parallel_shadow_learning_uses_new_activation_epoch_only():
    source = MIGRATION.read_text()
    assert "activated_at" in source
    assert "s.observed_at >= v_activation" in source
    assert "p.created_at >= v_activation" in source
    assert "d.created_at >= v_activation" in source
    assert "'old_forward_outcomes_allowed',false" in source


def test_shadow_learning_is_separate_from_real_reward_memory():
    source = MIGRATION.read_text()
    assert "v6_shadow_learning_transitions" in source
    assert "v6_shadow_learning_outcomes" in source
    assert "insert into public.marl_reward_memory" not in source.lower()
    assert "'marl_reward_memory_written',false" in source
    assert "'marl_reward_memory_used',false" in source


def test_counterfactual_evaluation_has_strict_180m_anti_leakage_gate():
    source = MIGRATION.read_text()
    assert "p.context_timestamp+interval '180 minutes'" in source
    assert "t.exit_candle_open_time + interval '28 minutes'" in source
    assert "counterfactual_fixed_horizon_exposure_gate_v1" in source
    assert "round_trip_cost',0.0015" in source


def test_btc_and_eth_replay_and_policy_versions_are_isolated():
    source = MIGRATION.read_text()
    assert "where symbol=v_symbol" in source
    assert "unique(symbol, sample_count)" in source
    assert "minimum_transitions_per_asset >= 20" in source
    assert "if v_n < 20 then" in source
    assert "'replay_isolation','per_asset'" in source


def test_safe_prior_can_only_reduce_exposure_and_does_not_train_risk_actor():
    source = MIGRATION.read_text()
    assert "v_half_kelly := greatest(0,least(1,0.5*v_kelly))" in source
    assert "max_weight numeric not null check (max_weight between 0 and 1)" in source
    assert "'only_reduce_base_risk',true" in source
    assert "'risk_trainable',false" in source
    assert "mappo_ready boolean not null default false" in source


def test_shadow_observer_reads_regime_aware_adaptive_v2_without_trading_authority():
    source = OBSERVER.read_text()
    assert '.from("v6_shadow_regime_policy_versions")' in source
    assert 'weights[symbol] = appliedWeight' in source
    assert '"v6-shadow-adaptive-v2"' in source
    assert "maximum_relative_risk: 1" in source
    assert "shadow_mode: true" in source
    assert "trading_authority: false" in source


def test_adaptive_v2_is_rolling_regime_aware_and_gradual():
    source = ADAPTIVE_MIGRATION.read_text()
    assert "'rolling_regime_aware_safe_prior_v2'" in source
    assert "'adaptive-v2',now(),now(),30,20,8,0.25,true" in source
    assert "limit 30" in source
    assert "limit 20" in source
    assert "'minimum_regime_samples',8" in source
    assert "target_weight in (0,0.25,0.5,0.75,1)" in source
    assert "v_prev+0.25" in source
    assert "v_prev-0.25" in source
    assert "v6_shadow_regime_policy_versions" in source


def test_adaptive_v2_penalizes_missed_opportunity_without_raising_base_risk():
    source = ADAPTIVE_MIGRATION.read_text()
    assert "v_wins4 >= 3 and v_sum4 > 0" in source
    assert "v_wins6 >= 4 and v_sum6 > 0" in source
    assert "maximum_relative_risk',1" in source
    assert "'only_reduce_base_risk',true" in source
    assert "'risk_trainable',false" in source
    assert "'trading_authority',false" in source


def test_adaptive_v2_scorecard_tracks_quality_not_just_trade_count():
    source = ADAPTIVE_MIGRATION.read_text()
    for metric in (
        "baseline_profit_factor",
        "shadow_profit_factor",
        "baseline_max_drawdown",
        "shadow_max_drawdown",
        "losses_avoided",
        "winners_missed",
        "avg_exposure",
        "exposure_efficiency",
    ):
        assert metric in source
    assert "validation_after_activation_only" in source
    assert "'lookahead_used',false" in source
