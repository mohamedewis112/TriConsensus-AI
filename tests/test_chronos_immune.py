from __future__ import annotations

from triconsensus.chronos import Intervention, MarketState
from triconsensus.chronos_immune import (
    AlphaImmuneSystem,
    FailureImmuneMemory,
    propose_minimal_causal_repairs,
)


def _state() -> MarketState:
    return MarketState(
        symbol="ETHUSDT",
        timestamp_ms=1_800_000_000_000,
        mid_price=3200.0,
        return_1m=0.002,
        return_5m=0.006,
        realized_volatility=0.018,
        bid_depth=1000.0,
        ask_depth=800.0,
        order_flow_imbalance=0.35,
        spread_bps=1.5,
        funding_rate=0.00005,
        open_interest_change=0.02,
        liquidation_imbalance=0.12,
        cross_venue_basis_bps=2.0,
        cross_venue_latency_ms=80.0,
        stablecoin_flow_z=0.6,
        dealer_gamma_z=-0.2,
        mempool_pressure_z=0.2,
        crowding_score=0.2,
    )


def _alpha_score(state: MarketState) -> float:
    liquidity = max(0.0, 1.0 - state.spread_bps / 10.0)
    return 0.75 * state.order_flow_imbalance + 0.20 * state.return_5m + 0.05 * liquidity


def test_vaccination_finds_minimum_fatal_intervention_and_never_gets_authority():
    interventions = (
        Intervention("small_liquidity_shock", {"bid_depth": 0.90, "ask_depth": 0.90, "spread_bps": 1.10}),
        Intervention("orderflow_reversal", {"order_flow_imbalance": -0.50}),
        Intervention("large_liquidity_shock", {"bid_depth": 0.40, "ask_depth": 0.40, "spread_bps": 2.00}),
    )
    result = AlphaImmuneSystem().vaccinate(
        alpha_id="eth_orderflow_continuation",
        state=_state(),
        score_fn=_alpha_score,
        interventions=interventions,
    )
    assert result.interventions_tested == 3
    assert result.survival_rate < 1.0
    assert result.minimum_fatal_intervention is not None
    assert result.paper_trading_authority is False
    assert result.live_trading_authority is False
    assert any("order_flow_imbalance" in failure.changed_factors for failure in result.failures)


def test_failure_memory_recognizes_recurrent_alpha_disease():
    interventions = (
        Intervention("orderflow_reversal", {"order_flow_imbalance": -0.50}),
        Intervention("liquidation_sell", {"liquidation_imbalance": -0.90}),
    )
    immune = AlphaImmuneSystem()
    memory = FailureImmuneMemory()
    for _ in range(3):
        result = immune.vaccinate(
            alpha_id="eth_orderflow_continuation",
            state=_state(),
            score_fn=_alpha_score,
            interventions=interventions,
        )
        memory.remember(result)
    recurrent = memory.recurrent_failures(
        alpha_id="eth_orderflow_continuation",
        minimum_count=2,
    )
    assert recurrent
    assert memory.counts()[recurrent[0].fingerprint] >= 2


def test_minimal_repair_generator_proposes_guard_not_retraining():
    interventions = (
        Intervention("orderflow_reversal", {"order_flow_imbalance": -0.50}),
        Intervention("crowded_funding", {"funding_rate": 0.001, "crowding_score": 0.90}),
    )
    result = AlphaImmuneSystem().vaccinate(
        alpha_id="eth_orderflow_continuation",
        state=_state(),
        score_fn=_alpha_score,
        interventions=interventions,
    )
    repairs = propose_minimal_causal_repairs(result)
    assert repairs
    assert all(repair.research_only for repair in repairs)
    assert all(not repair.paper_trading_authority for repair in repairs)
    assert any(repair.guard == "require_orderflow_alignment" for repair in repairs)
