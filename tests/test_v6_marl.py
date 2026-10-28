from datetime import UTC, datetime

import pytest

from triconsensus.config import Settings
from triconsensus.risk import DeterministicRiskEngine, RiskInput
from triconsensus.v6_agents import (
    DynamicVolatilityRiskAgent,
    MetaRLPortfolioAdapterAgent,
)
from triconsensus.v6_models import (
    AssetRollingStats,
    DynamicRiskAction,
    PortfolioAction,
    PortfolioState,
    RewardMemoryRecord,
    VolatilityRiskState,
)
from triconsensus.v6_reward import build_portfolio_state, reward_value


def risk_input(**overrides):
    data = dict(
        equity=10_000,
        day_start_equity=10_000,
        daily_pnl=0,
        peak_equity=10_000,
        open_positions=0,
        current_exposure=0,
        correlated_exposure=0,
        losing_streak=0,
        minutes_since_last_loss=999999,
        kill_switch_active=False,
        entry=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        spread_bps=1.0,
        slippage_bps=2.0,
        realized_volatility_20=0.02,
        market_data_age_ms=100,
        side="LONG",
        position_weight=1.0,
    )
    data.update(overrides)
    return RiskInput(**data)


def test_reward_is_net_return_minus_drawdown_penalty():
    reward = reward_value(
        net_return=0.012,
        max_drawdown=0.003,
        drawdown_penalty=2.0,
    )
    assert reward == pytest.approx(0.006)


def test_reward_memory_warmup_is_neutral_not_random():
    state = build_portfolio_state([], window=40, minimum_warmup_trades_per_asset=20)
    action = MetaRLPortfolioAdapterAgent(shadow_mode=True).decide(state)
    assert action.asset_weights["BTC/USDT"] == pytest.approx(1.0)
    assert action.asset_weights["ETH/USDT"] == pytest.approx(1.0)
    assert action.blacklisted_assets == []
    assert action.reason == "warmup_insufficient_reward_memory"


def test_consecutive_losses_can_hard_blacklist_asset():
    rows = [
        RewardMemoryRecord(
            symbol="ETH/USDT",
            trade_id=f"00000000-0000-0000-0000-{i:012d}",
            policy_version="shadow",
            state={},
            action={},
            net_return=-0.004,
            max_drawdown=0.01,
            reward=-0.024,
            created_at=datetime.now(UTC),
        )
        for i in range(8)
    ]
    state = build_portfolio_state(rows, window=40, minimum_warmup_trades_per_asset=20)
    action = MetaRLPortfolioAdapterAgent(
        shadow_mode=False,
        minimum_blacklist_samples=8,
        hard_losing_streak=4,
        hard_mean_net_return=-0.003,
    ).decide(state)
    assert "ETH/USDT" in action.blacklisted_assets
    assert action.asset_weights["ETH/USDT"] == pytest.approx(0.0)


def test_portfolio_action_rejects_weight_above_one():
    with pytest.raises(ValueError):
        PortfolioAction(
            asset_weights={"BTC/USDT": 1.1, "ETH/USDT": 0.5},
            policy_version="bad",
            reason="test",
        )


def test_dynamic_risk_projection_stays_inside_action_bounds():
    state = VolatilityRiskState(
        symbol="BTC/USDT",
        side="LONG",
        entry_price=100.0,
        atr_14=2.0,
        realized_volatility=0.40,
        implied_volatility=0.80,
        bybit_orderbook_imbalance=0.05,
        binance_orderbook_imbalance=0.02,
        spread_bps=1.0,
        market_regime="trend_up",
        microstructure_age_ms=100,
    )
    action = DynamicVolatilityRiskAgent(shadow_mode=False).decide(state)
    assert 0.75 <= action.stop_atr_multiplier <= 2.25
    assert 0.75 <= action.reward_to_risk_multiplier <= 1.50
    assert 0.0 <= action.trailing_lock_fraction <= 0.90
    assert action.stop_atr_multiplier > 1.5
    assert action.trailing_lock_fraction >= pytest.approx(0.35)


def test_cross_venue_adverse_orderbook_can_force_no_trade():
    state = VolatilityRiskState(
        symbol="ETH/USDT",
        side="SHORT",
        entry_price=2300.0,
        atr_14=30.0,
        realized_volatility=0.50,
        implied_volatility=0.55,
        bybit_orderbook_imbalance=0.60,
        binance_orderbook_imbalance=0.55,
        spread_bps=1.0,
        market_regime="trend_up",
        microstructure_age_ms=50,
    )
    action = DynamicVolatilityRiskAgent(shadow_mode=False).decide(state)
    assert action.force_no_trade is True
    assert "cross_venue_orderbook_veto_short" in action.reason


def test_adaptive_weight_can_only_reduce_deterministic_position_size():
    engine = DeterministicRiskEngine(Settings(_env_file=None))
    full = engine.evaluate(risk_input(position_weight=1.0))
    half = engine.evaluate(risk_input(position_weight=0.5))

    assert full.approved is True
    assert half.approved is True
    assert half.position_size == pytest.approx(full.position_size * 0.5)
    assert half.projected_exposure == pytest.approx(full.projected_exposure * 0.5)


def test_zero_weight_is_vetoed_by_deterministic_risk_engine():
    decision = DeterministicRiskEngine(Settings(_env_file=None)).evaluate(
        risk_input(position_weight=0.0)
    )
    assert decision.approved is False
    assert decision.reason == "adaptive_weight_zero"


def test_dynamic_levels_still_obey_deterministic_minimum_risk_reward():
    settings = Settings(_env_file=None)
    agent = DynamicVolatilityRiskAgent(shadow_mode=False)
    action = DynamicRiskAction(
        stop_atr_multiplier=2.25,
        reward_to_risk_multiplier=0.75,
        trailing_lock_fraction=0.50,
        policy_version="test",
        reason="test",
        shadow_mode=False,
    )
    levels = agent.adapt_levels(
        last_price=100.0,
        atr=2.0,
        minimum_risk_reward=settings.min_risk_reward,
        action=action,
    )
    long = levels.long

    decision = DeterministicRiskEngine(settings).evaluate(
        risk_input(
            entry=long["entry"],
            stop_loss=long["stop_loss"],
            take_profit=long["take_profit_1"],
            position_weight=0.5,
        )
    )
    assert decision.approved is True
    assert decision.risk_reward == pytest.approx(settings.min_risk_reward)


def test_deterministic_risk_engine_has_final_veto_after_rl_action():
    settings = Settings(_env_file=None)
    decision = DeterministicRiskEngine(settings).evaluate(
        risk_input(
            position_weight=1.0,
            daily_pnl=-(10_000 * settings.max_daily_loss),
        )
    )
    assert decision.approved is False
    assert decision.reason == "daily_loss_limit"
