from __future__ import annotations

import math

import numpy as np

from triconsensus.chronos import (
    CHRONOS_LIVE_TRADING_AUTHORITY,
    CHRONOS_PAPER_TRADING_AUTHORITY,
    ChronosWorldEngine,
    LatentActorDetector,
    MarketPhysicsMiner,
    MarketState,
    PhysicsObservation,
    build_causal_survival_passport,
    build_intent_field,
    default_digital_minds,
    run_chronos_research_cycle,
)


def _state(**overrides) -> MarketState:
    data = {
        "symbol": "ETHUSDT",
        "timestamp_ms": 1_800_000_000_000,
        "mid_price": 3200.0,
        "return_1m": 0.002,
        "return_5m": 0.007,
        "realized_volatility": 0.018,
        "bid_depth": 850.0,
        "ask_depth": 650.0,
        "order_flow_imbalance": 0.28,
        "spread_bps": 1.8,
        "funding_rate": 0.00012,
        "open_interest_change": 0.025,
        "liquidation_imbalance": 0.18,
        "cross_venue_basis_bps": 2.5,
        "cross_venue_latency_ms": 85.0,
        "stablecoin_flow_z": 0.8,
        "dealer_gamma_z": -0.4,
        "mempool_pressure_z": 0.5,
        "crowding_score": 0.22,
    }
    data.update(overrides)
    return MarketState(**data)


def test_intent_field_is_explainable_and_actor_based():
    field = build_intent_field(_state(), default_digital_minds())
    assert len(field.contributions) == 8
    assert -1.0 <= field.net_pressure <= 1.0
    assert 0.0 <= field.confidence <= 1.0
    assert field.dominant_actor
    assert field.research_only is True
    assert {c.actor_kind for c in field.contributions} >= {
        "momentum_fund",
        "market_maker",
        "arbitrage",
        "liquidation_engine",
    }


def test_future_landscape_probabilities_and_diversity_are_valid():
    state = _state()
    field = build_intent_field(state)
    result = ChronosWorldEngine().simulate(
        state, field, universes=1500, horizon_steps=12, seed=41
    )
    assert result.positive_probability + result.negative_probability + result.neutral_probability == pytest_approx(1.0)
    assert 0.0 <= result.future_diversity <= 1.0
    assert result.effective_scenarios >= 1.0
    assert result.p05_return <= result.median_return <= result.p95_return


def pytest_approx(value: float, tolerance: float = 1e-9):
    class Approx:
        def __eq__(self, other):
            return math.isclose(float(other), value, rel_tol=0.0, abs_tol=tolerance)
    return Approx()


def test_action_and_no_action_twins_share_the_same_random_worlds():
    state = _state()
    field = build_intent_field(state)
    twins = ChronosWorldEngine().paired_action_twins(
        state,
        field,
        action_notional_fraction=0.10,
        universes=1200,
        horizon_steps=10,
        seed=9,
    )
    assert twins.same_random_worlds is True
    assert twins.trading_authority is False
    assert twins.self_impact_bps > 0
    assert twins.median_counterfactual_delta != 0


def test_latent_actor_detector_finds_persistent_unexplained_flow():
    detector = LatentActorDetector(window=30, minimum_history=12, z_threshold=2.0)
    for i in range(14):
        detector.observe(predicted_order_flow=0.10, observed_order_flow=0.11 + (i % 2) * 0.005)
    evidence = None
    for _ in range(7):
        evidence = detector.observe(predicted_order_flow=0.10, observed_order_flow=0.55)
    assert evidence is not None
    assert evidence.detected is True
    assert evidence.direction == "BUY_PRESSURE"
    assert evidence.actor_id.startswith("LATENT-")


def test_market_physics_miner_recovers_a_stable_synthetic_orderflow_law():
    rng = np.random.default_rng(123)
    observations = []
    for i in range(180):
        ofi = float(rng.uniform(-0.9, 0.9))
        state = _state(
            timestamp_ms=1_800_000_000_000 + i * 60_000,
            order_flow_imbalance=ofi,
            return_5m=float(rng.normal(0, 0.002)),
            liquidation_imbalance=float(rng.uniform(-0.2, 0.2)),
        )
        next_return = 0.008 * ofi + float(rng.normal(0, 0.0012))
        observations.append(PhysicsObservation(state=state, next_return=next_return))
    laws = MarketPhysicsMiner(
        minimum_support=90,
        minimum_abs_correlation=0.20,
    ).discover(observations)
    assert laws
    orderflow = next(law for law in laws if law.name == "orderflow")
    assert orderflow.correlation > 0.7
    assert orderflow.fold_sign_consistency == 1.0


def test_causal_passport_stays_research_only():
    passport = build_causal_survival_passport(
        _state(),
        universes=600,
        horizon_steps=8,
        seed=55,
    )
    assert passport.interventions_tested >= 6
    assert 0.0 <= passport.survival_rate <= 1.0
    assert passport.trading_authority is False
    assert passport.research_only is True


def test_full_chronos_cycle_has_zero_paper_and_live_authority():
    report = run_chronos_research_cycle(
        _state(),
        universes=1000,
        horizon_steps=8,
        seed=77,
    )
    assert report.version.startswith("v8.0-chronos")
    assert report.research_candidate in {"WATCH_LONG", "WATCH_SHORT", "NO_RESEARCH_EDGE"}
    assert report.paper_trading_authority is False
    assert report.live_trading_authority is False
    assert CHRONOS_PAPER_TRADING_AUTHORITY is False
    assert CHRONOS_LIVE_TRADING_AUTHORITY is False
    assert report.action_twin.trading_authority is False
