from __future__ import annotations

import numpy as np

from triconsensus.chronos import MarketState
from triconsensus.chronos_learning import ChronosTransition, ProbabilisticTransitionEnsemble
from triconsensus.chronos_real_shadow import _evaluation_metrics


def test_real_shadow_evaluation_metrics_are_bounded_and_authority_free():
    rng = np.random.default_rng(2700)
    rows = []
    for i in range(260):
        ofi = float(rng.uniform(-0.8, 0.8))
        state = MarketState(
            symbol="ETHUSDT",
            timestamp_ms=1_800_000_000_000 + i * 60_000,
            mid_price=3200.0,
            return_1m=0.0005 * ofi,
            return_5m=0.0015 * ofi,
            realized_volatility=0.01,
            bid_depth=1000.0 + 100.0 * ofi,
            ask_depth=900.0 - 100.0 * ofi,
            order_flow_imbalance=ofi,
            spread_bps=1.5,
        )
        rows.append(
            ChronosTransition(
                state=state,
                next_return=0.004 * ofi + float(rng.normal(0, 0.0004)),
                next_order_flow=float(np.clip(0.7 * ofi + rng.normal(0, 0.04), -1, 1)),
                next_liquidity_change=float(np.clip(-0.1 * abs(ofi), -1, 1)),
            )
        )
    model = ProbabilisticTransitionEnsemble(heads=5, minimum_samples=180)
    report = model.fit(rows)
    metrics = _evaluation_metrics(model, rows, report.evaluation_rows)
    assert metrics["rows"] == report.evaluation_rows
    assert 0.0 <= metrics["directional_accuracy"] <= 1.0
    assert 0.0 <= metrics["interval_coverage"] <= 1.0
    assert metrics["mae"] >= 0.0
    assert metrics["rmse"] >= 0.0
