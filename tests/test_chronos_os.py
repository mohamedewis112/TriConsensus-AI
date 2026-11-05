from __future__ import annotations

import numpy as np

from triconsensus.chronos import MarketState
from triconsensus.chronos_firewall import (
    ParameterPoint,
    research_firewall,
)
from triconsensus.chronos_hybrid import ChronosArtificialMarketCivilization
from triconsensus.chronos_learning import ChronosTransition
from triconsensus.chronos_memory import MarketMemoryIndex
from triconsensus.chronos_os import ChronosResearchOperatingSystem


def _dataset(n: int = 360):
    rng = np.random.default_rng(88)
    rows = []
    base = 1_800_000_000_000
    for i in range(n):
        ofi = float(rng.uniform(-0.75, 0.75))
        ret5 = float(rng.normal(0, 0.0035))
        state = MarketState(
            symbol="ETHUSDT",
            timestamp_ms=base + i * 60_000,
            mid_price=3200.0,
            return_1m=0.3 * ret5,
            return_5m=ret5,
            realized_volatility=0.014,
            bid_depth=1300 + 200 * ofi,
            ask_depth=1250 - 200 * ofi,
            order_flow_imbalance=ofi,
            spread_bps=1.4,
            funding_rate=0.00005 * np.sign(ret5),
            open_interest_change=0.01,
            liquidation_imbalance=0.1 * np.sign(ret5),
            cross_venue_basis_bps=1.5 * np.sign(ret5),
            cross_venue_latency_ms=70.0,
            stablecoin_flow_z=0.4 * ofi,
            dealer_gamma_z=-0.2,
            mempool_pressure_z=0.1,
            crowding_score=0.15 * np.sign(ret5),
        )
        rows.append(
            ChronosTransition(
                state=state,
                next_return=0.008 * ofi + 0.45 * ret5 + float(rng.normal(0, 0.0004)),
                next_order_flow=float(np.clip(0.7 * ofi + rng.normal(0, 0.05), -1, 1)),
                next_liquidity_change=float(np.clip(-0.10 * abs(ofi) + rng.normal(0, 0.02), -1, 1)),
            )
        )
    return rows


def _firewall(pass_like: bool = True):
    rng = np.random.default_rng(9)
    n = 320
    candidate = rng.normal(0.002 if pass_like else -0.001, 0.009, size=n)
    trials = np.column_stack(
        [
            candidate,
            rng.normal(-0.0002, 0.010, size=n),
            rng.normal(0.0001, 0.010, size=n),
            rng.normal(-0.0001, 0.011, size=n),
        ]
    )
    points = [
        ParameterPoint(10, 0.65),
        ParameterPoint(11, 0.77),
        ParameterPoint(12, 0.82),
        ParameterPoint(13, 0.80),
        ParameterPoint(14, 0.75),
        ParameterPoint(15, 0.50),
    ]
    return research_firewall(
        candidate_returns=candidate,
        all_trial_returns=trials,
        parameter_points=points,
        maximum_pbo=1.0,
        minimum_deflated_probability=0.0,
    )


def _score(state: MarketState) -> float:
    return 0.75 * state.order_flow_imbalance + 0.25 * state.return_5m


def test_os_hard_rejects_failed_firewall_even_if_other_layers_exist():
    data = _dataset()
    civilization = ChronosArtificialMarketCivilization()
    civilization.fit(data)
    memory = MarketMemoryIndex(minimum_neighbors=5, similarity_floor=0.45, max_neighbors=25)
    memory.fit_transitions(data)
    os = ChronosResearchOperatingSystem(civilization, memory, minimum_vaccination_survival=0.0)

    report = os.assess(
        alpha_id="eth_test_alpha",
        state=data[-1].state,
        alpha_score_fn=_score,
        firewall=_firewall(pass_like=False),
        drift_detected=False,
        universes=1200,
        horizon_steps=8,
    )
    assert report.verdict == "REJECT_RESEARCH"
    assert "research_firewall_failed" in report.reasons
    assert report.paper_trading_authority is False
    assert report.live_trading_authority is False


def test_os_drift_forces_research_rejection():
    data = _dataset()
    civilization = ChronosArtificialMarketCivilization()
    civilization.fit(data)
    memory = MarketMemoryIndex(minimum_neighbors=5, similarity_floor=0.45, max_neighbors=25)
    memory.fit_transitions(data)
    os = ChronosResearchOperatingSystem(civilization, memory, minimum_vaccination_survival=0.0)

    report = os.assess(
        alpha_id="eth_test_alpha",
        state=data[-1].state,
        alpha_score_fn=_score,
        firewall=_firewall(pass_like=True),
        drift_detected=True,
        universes=1200,
        horizon_steps=8,
    )
    assert report.verdict == "REJECT_RESEARCH"
    assert "world_model_not_trusted" in report.reasons


def test_os_never_promotes_past_watch_to_execution_authority():
    data = _dataset()
    civilization = ChronosArtificialMarketCivilization()
    civilization.fit(data)
    memory = MarketMemoryIndex(minimum_neighbors=5, similarity_floor=0.40, max_neighbors=30)
    memory.fit_transitions(data)
    os = ChronosResearchOperatingSystem(civilization, memory, minimum_vaccination_survival=0.0)

    report = os.assess(
        alpha_id="eth_test_alpha",
        state=data[-1].state,
        alpha_score_fn=_score,
        firewall=_firewall(pass_like=True),
        drift_detected=False,
        universes=1400,
        horizon_steps=8,
    )
    assert report.verdict in {
        "WATCH_LONG",
        "WATCH_SHORT",
        "HOLD_FOR_EVIDENCE",
        "REJECT_RESEARCH",
    }
    assert report.paper_trading_authority is False
    assert report.live_trading_authority is False
    assert report.civilization.paper_trading_authority is False
