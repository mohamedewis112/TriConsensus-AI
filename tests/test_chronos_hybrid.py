from __future__ import annotations

import numpy as np

from triconsensus.chronos import MarketState
from triconsensus.chronos_hybrid import ChronosArtificialMarketCivilization
from triconsensus.chronos_learning import ChronosTransition


def _data(n: int = 360) -> list[ChronosTransition]:
    rng = np.random.default_rng(2027)
    rows = []
    for i in range(n):
        ofi = float(rng.uniform(-0.8, 0.8))
        ret5 = float(rng.normal(0, 0.004))
        state = MarketState(
            symbol="BTCUSDT",
            timestamp_ms=1_800_000_000_000 + i * 60_000,
            mid_price=70_000.0 * (1.0 + i * 0.00001),
            return_1m=0.35 * ret5,
            return_5m=ret5,
            realized_volatility=0.016,
            bid_depth=1500.0 + 250.0 * ofi,
            ask_depth=1450.0 - 250.0 * ofi,
            order_flow_imbalance=ofi,
            spread_bps=1.3 + 0.7 * abs(ofi),
            funding_rate=0.00005 * np.sign(ret5),
            open_interest_change=0.012,
            liquidation_imbalance=0.12 * np.sign(ret5),
            cross_venue_basis_bps=2.0 * ret5 / 0.004,
            cross_venue_latency_ms=65.0,
            stablecoin_flow_z=0.4 * ofi,
            dealer_gamma_z=-0.2,
            mempool_pressure_z=0.15,
            crowding_score=0.15 * np.sign(ret5),
        )
        rows.append(
            ChronosTransition(
                state=state,
                next_return=0.010 * ofi + 0.45 * ret5 + float(rng.normal(0, 0.00035)),
                next_order_flow=float(np.clip(0.75 * ofi + rng.normal(0, 0.05), -1, 1)),
                next_liquidity_change=float(
                    np.clip(-0.12 * abs(ofi) + rng.normal(0, 0.025), -1, 1)
                ),
            )
        )
    return rows


def test_civilization_uses_learned_world_only_when_gate_is_ready():
    data = _data()
    system = ChronosArtificialMarketCivilization()
    system.fit(data)
    report = system.forecast(
        data[-1].state,
        drift_detected=False,
        universes=1500,
        horizon_steps=8,
        seed=2700,
    )
    assert report.world_mode == "learned"
    assert report.transition is not None
    assert report.trust.research_ready is True
    assert report.paper_trading_authority is False
    assert report.live_trading_authority is False
    assert report.action_twin.same_random_worlds is True
    assert len(report.learned_actor_ranking) == 8


def test_drift_forces_transparent_fallback_and_blocks_research_edge():
    data = _data()
    system = ChronosArtificialMarketCivilization()
    system.fit(data)
    report = system.forecast(
        data[-1].state,
        drift_detected=True,
        universes=1200,
        horizon_steps=8,
        seed=99,
    )
    assert report.world_mode == "transparent_fallback"
    assert report.transition is None
    assert report.trust.drift_detected is True
    assert report.trust.research_ready is False
    assert report.research_candidate == "NO_RESEARCH_EDGE"


def test_learned_multiverse_is_probabilistic_not_single_path():
    data = _data()
    system = ChronosArtificialMarketCivilization()
    system.fit(data)
    report = system.forecast(
        data[-1].state,
        drift_detected=False,
        universes=2000,
        horizon_steps=12,
        seed=123,
    )
    assert report.future.universes == 2000
    assert report.future.p05_return < report.future.p95_return
    assert 0.0 <= report.future.future_diversity <= 1.0
    assert report.future.effective_scenarios > 1.0
