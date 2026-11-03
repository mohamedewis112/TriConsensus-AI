from __future__ import annotations

import numpy as np
import pytest

from triconsensus.chronos import MarketState
from triconsensus.chronos_learning import (
    AdaptiveDigitalMindWeights,
    ChronosTransition,
    ProbabilisticTransitionEnsemble,
    SequentialResidualDriftDetector,
    evaluate_learning_gate,
)


def _state(i: int, *, rng: np.random.Generator) -> MarketState:
    ofi = float(rng.uniform(-0.8, 0.8))
    momentum = float(rng.normal(0.0, 0.004))
    return MarketState(
        symbol="ETHUSDT",
        timestamp_ms=1_800_000_000_000 + i * 60_000,
        mid_price=3200.0 * (1.0 + 0.0001 * i),
        return_1m=0.4 * momentum,
        return_5m=momentum,
        realized_volatility=0.012 + 0.008 * abs(momentum) / 0.004,
        bid_depth=900.0 + 120.0 * ofi,
        ask_depth=850.0 - 120.0 * ofi,
        order_flow_imbalance=ofi,
        spread_bps=1.2 + 0.7 * abs(ofi),
        funding_rate=0.00005 * np.sign(momentum),
        open_interest_change=0.01 + 0.03 * momentum,
        liquidation_imbalance=0.15 * np.sign(momentum),
        cross_venue_basis_bps=2.0 * momentum / 0.004,
        cross_venue_latency_ms=75.0,
        stablecoin_flow_z=0.5 * ofi,
        dealer_gamma_z=-0.3,
        mempool_pressure_z=0.2,
        crowding_score=0.25 * np.sign(momentum),
    )


def _dataset(n: int = 360) -> list[ChronosTransition]:
    rng = np.random.default_rng(91)
    rows = []
    for i in range(n):
        state = _state(i, rng=rng)
        noise = float(rng.normal(0.0, 0.00035))
        next_return = (
            0.010 * state.order_flow_imbalance
            + 0.55 * state.return_5m
            - 0.00003 * state.spread_bps
            + noise
        )
        next_ofi = float(
            np.clip(
                0.72 * state.order_flow_imbalance
                + 0.18 * np.tanh(30.0 * state.return_5m)
                + rng.normal(0.0, 0.05),
                -1.0,
                1.0,
            )
        )
        liquidity_change = float(
            np.clip(
                -0.16 * abs(state.order_flow_imbalance)
                - 0.08 * state.liquidity_thinness
                + rng.normal(0.0, 0.025),
                -1.0,
                1.0,
            )
        )
        rows.append(
            ChronosTransition(
                state=state,
                next_return=next_return,
                next_order_flow=next_ofi,
                next_liquidity_change=liquidity_change,
            )
        )
    return rows


def test_probabilistic_world_model_learns_synthetic_transition_and_calibrates():
    data = _dataset()
    model = ProbabilisticTransitionEnsemble(heads=7, minimum_samples=180, seed=2700)
    report = model.fit(data)
    assert report.train_rows > report.calibration_rows >= 40
    assert report.evaluation_rows >= 40
    assert report.calibrated is True
    assert report.mean_interval_coverage >= 0.72

    forecast = model.predict(data[-1].state)
    assert forecast.research_only is True
    assert forecast.paper_trading_authority is False
    assert forecast.live_trading_authority is False
    assert forecast.next_return_low <= forecast.next_return_mean <= forecast.next_return_high
    assert -1.0 <= forecast.next_order_flow_mean <= 1.0


def test_learning_gate_is_research_only_and_drift_can_close_it():
    model = ProbabilisticTransitionEnsemble(heads=5, minimum_samples=180)
    model.fit(_dataset())
    good = evaluate_learning_gate(model, drift_detected=False)
    assert good.research_ready is True
    assert good.paper_trading_authority is False
    assert good.live_trading_authority is False

    bad = evaluate_learning_gate(model, drift_detected=True)
    assert bad.research_ready is False
    assert "distribution_drift_detected" in bad.reasons


def test_residual_drift_detector_trips_after_persistent_distribution_shift():
    detector = SequentialResidualDriftDetector(z_threshold=3.0, consecutive_required=3)
    rng = np.random.default_rng(7)
    baseline = rng.normal(0.0, 0.02, size=(100, 3))
    detector.fit_baseline(baseline)

    status = None
    for _ in range(3):
        status = detector.update([0.45, 0.55, -0.40])
    assert status is not None
    assert status.drift_detected is True
    assert status.consecutive_breaches >= 3


def test_adaptive_digital_minds_learn_which_behavioural_prior_explains_flow():
    rng = np.random.default_rng(12)
    learner = AdaptiveDigitalMindWeights(decay=0.94)
    for i in range(220):
        state = _state(i, rng=rng)
        # Realised flow deliberately follows momentum-like continuation.
        realised = float(np.clip(
            0.55 * np.tanh(30.0 * state.return_5m)
            + 0.45 * state.order_flow_imbalance,
            -1.0,
            1.0,
        ))
        learner.observe(state, realised_next_order_flow=realised)

    skills = {row.actor: row for row in learner.skills()}
    assert skills["Momentum Funds"].observations == 220
    assert skills["Momentum Funds"].learned_skill > 0.50
    assert skills["Momentum Funds"].effective_weight > 0.0


def test_model_refuses_tiny_sample():
    model = ProbabilisticTransitionEnsemble(minimum_samples=180)
    with pytest.raises(ValueError, match="insufficient CHRONOS learning sample"):
        model.fit(_dataset(100))
