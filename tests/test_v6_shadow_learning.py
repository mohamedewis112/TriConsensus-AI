from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20261029012009_add_v6_parallel_shadow_learning.sql"
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


def test_shadow_observer_reads_learned_per_asset_caps_without_trading_authority():
    source = OBSERVER.read_text()
    assert '.from("v6_shadow_asset_policy_versions")' in source
    assert 'weights[symbol] = Math.min(weights[symbol], cap)' in source
    assert '"v6-shadow-parallel-safe-prior"' in source
    assert "shadow_mode: true" in source
    assert "trading_authority: false" in source
