from __future__ import annotations

import numpy as np
from triconsensus.chronos import MarketState
from triconsensus.chronos_immediate_ab import evaluate


def test_ab_smoke_is_cost_aware_and_has_no_authority():
    rng = np.random.default_rng(824)
    states = []
    price = 3200.0
    for i in range(235):
        ofi = float(rng.uniform(-0.8, 0.8))
        price *= 1.0 + 0.00008 * ofi + float(rng.normal(0, 0.00002))
        states.append(MarketState(
            symbol="ETHUSDT",
            timestamp_ms=1_800_000_000_000 + i * 1_000,
            mid_price=price,
            return_1m=0.001 * ofi,
            return_5m=0.002 * ofi,
            realized_volatility=0.0002,
            bid_depth=1_200_000 + 150_000 * ofi,
            ask_depth=1_100_000 - 150_000 * ofi,
            order_flow_imbalance=ofi,
            spread_bps=1.5,
            funding_rate=0.00005,
            open_interest_change=0.0,
            cross_venue_basis_bps=0.5 * ofi,
        ))
    report = evaluate(states)
    assert report["paper_trading_authority"] is False
    assert report["live_trading_authority"] is False
    assert len(report["horizons"]) == 3
    assert all("improvement_per_opportunity" in row for row in report["horizons"])
