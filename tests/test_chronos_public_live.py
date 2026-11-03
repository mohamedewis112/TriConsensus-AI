from __future__ import annotations

import numpy as np

from triconsensus.chronos import MarketState
from triconsensus.chronos_public_live import analyze_symbol


def test_public_live_analysis_stays_research_only():
    rng = np.random.default_rng(81)
    states = []
    mid = 3200.0
    for i in range(230):
        ofi = float(rng.uniform(-0.7, 0.7))
        mid *= 1.0 + 0.00008 * ofi + float(rng.normal(0, 0.00003))
        states.append(
            MarketState(
                symbol="ETHUSDT",
                timestamp_ms=1_800_000_000_000 + i * 1_000,
                mid_price=mid,
                return_1m=0.001 * ofi,
                return_5m=0.002 * ofi,
                realized_volatility=0.0002,
                bid_depth=1200 + 150 * ofi,
                ask_depth=1100 - 150 * ofi,
                order_flow_imbalance=ofi,
                spread_bps=1.4,
                funding_rate=0.00005,
                open_interest_change=0.0002,
                cross_venue_basis_bps=0.5 * ofi,
            )
        )
    report = analyze_symbol(states, "ETHUSDT", universes=1200)
    assert report["real_transitions"] >= 180
    assert report["paper_trading_authority"] is False
    assert report["live_trading_authority"] is False
    assert 0.0 <= report["untouched_evaluation"]["directional_accuracy"] <= 1.0
