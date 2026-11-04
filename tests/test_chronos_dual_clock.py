from __future__ import annotations

from dataclasses import replace

import numpy as np

from triconsensus.chronos import MarketState
from triconsensus.chronos_dual_clock import (
    DualClockChronos,
    SlowAlphaEvidence,
    head_specific_trust,
)
from triconsensus.chronos_learning import (
    ChronosTransition,
    ProbabilisticTransitionEnsemble,
)
from triconsensus.v7_signal_lab import V7Candidate


def _data(n: int = 420) -> list[ChronosTransition]:
    rng = np.random.default_rng(1818)
    rows: list[ChronosTransition] = []
    for i in range(n):
        ofi = float(rng.uniform(-0.8, 0.8))
        ret5 = float(rng.normal(0.0, 0.003))
        state = MarketState(
            symbol="ETHUSDT",
            timestamp_ms=1_800_000_000_000 + i * 1_000,
            mid_price=3200.0,
            return_1m=0.25 * ret5,
            return_5m=ret5,
            realized_volatility=0.012,
            bid_depth=1_200_000.0 + 180_000.0 * ofi,
            ask_depth=1_150_000.0 - 180_000.0 * ofi,
            order_flow_imbalance=ofi,
            spread_bps=1.5,
            funding_rate=0.00005,
            open_interest_change=0.01,
            cross_venue_basis_bps=1.5 * ofi,
        )
        rows.append(
            ChronosTransition(
                state=state,
                # Deliberately unlearnable price head.
                next_return=float(rng.normal(0.0, 0.0015)),
                # Learnable fast microstructure heads.
                next_order_flow=float(
                    np.clip(0.62 * ofi + 0.20 * np.tanh(20 * ret5)
                    + rng.normal(0, 0.025), -1, 1)
                ),
                next_liquidity_change=float(
                    0.35 * state.depth_imbalance + rng.normal(0, 0.015)
                ),
            )
        )
    return rows


def _validated_long() -> SlowAlphaEvidence:
    candidate = V7Candidate(
        species="trend_breakout",
        side="LONG",
        confidence=0.72,
        reason="test",
        regime_key="trend_up|mid_vol|momentum_up",
        research_mode="historical_research_only",
    )
    return SlowAlphaEvidence.from_v7(
        candidate,
        alpha_id="eth_slow_trend_long",
        holdout_trades=80,
        holdout_hit_rate=0.58,
        holdout_mean_net_return=0.0012,
        profit_factor=1.55,
        profitable_folds=3,
    )


def test_head_trust_can_reject_price_but_keep_fast_microstructure():
    model = ProbabilisticTransitionEnsemble(
        heads=7,
        minimum_samples=180,
        seed=1818,
    )
    model.fit(_data())
    trust = head_specific_trust(model.calibration_report)
    assert trust.price_head_trusted is False
    assert trust.orderflow_head_trusted is True
    assert trust.liquidity_head_trusted is True
    assert trust.fast_execution_trusted is True
    assert trust.orderflow_relative_skill > 0.0
    assert trust.liquidity_relative_skill > 0.0


def test_dual_clock_fast_layer_cannot_create_direction_but_can_time_validated_alpha():
    model = ProbabilisticTransitionEnsemble(
        heads=7,
        minimum_samples=180,
        seed=1818,
    )
    data = _data()
    model.fit(data)
    state = next(row.state for row in reversed(data) if row.state.order_flow_imbalance > 0.35)
    decision = DualClockChronos(model).assess(
        slow_alpha=_validated_long(),
        state=state,
    )
    assert decision.slow_alpha_validated is True
    assert decision.fast_execution_trusted is True
    assert decision.price_head_trusted is False
    assert decision.verdict in {"SHADOW_TIMING_LONG", "VETO_FAST_EXECUTION"}
    assert decision.paper_trading_authority is False
    assert decision.live_trading_authority is False


def test_dual_clock_rejects_unvalidated_slow_alpha_before_fast_model():
    model = ProbabilisticTransitionEnsemble(
        heads=5,
        minimum_samples=180,
        seed=1818,
    )
    data = _data()
    model.fit(data)
    invalid = replace(
        _validated_long(),
        holdout_hit_rate=0.40,
        evidence_gate_passed=False,
    )
    decision = DualClockChronos(model).assess(
        slow_alpha=invalid,
        state=data[-1].state,
    )
    assert decision.verdict == "REJECT_SLOW_ALPHA"
    assert decision.fast_forecast is None
