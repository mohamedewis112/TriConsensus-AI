from __future__ import annotations

import numpy as np

from triconsensus.chronos_ws_challenger import (
    WsFastExecutionModel,
    build_rows,
)
from triconsensus.chronos_ws_nervous_system import NervousFeatureFrame


def _frames(n: int = 340):
    rng = np.random.default_rng(821)
    frames = []
    flow = 0.0
    spread = 0.04
    micro = 0.0
    for i in range(n):
        pressure = float(rng.normal(0, 0.6))
        flow = float(np.clip(0.72 * flow + 0.22 * pressure + rng.normal(0, 0.08), -1, 1))
        spread = float(max(0.005, 0.75 * spread + 0.012 * abs(pressure) + rng.normal(0, 0.003)))
        micro = float(0.65 * micro + 0.025 * pressure + rng.normal(0, 0.006))
        frames.append(
            NervousFeatureFrame(
                symbol="ETHUSDT",
                timestamp_ms=1_800_000_000_000 + i * 250,
                mid_price=3000.0 + i * 0.01,
                spread_bps=spread,
                microprice_offset_bps=micro,
                imbalance_l1=float(np.clip(0.6 * pressure, -1, 1)),
                imbalance_l5=float(np.clip(0.45 * pressure, -1, 1)),
                book_pressure_slope=float(np.tanh(pressure)),
                trade_imbalance_1s=flow,
                trade_imbalance_5s=float(np.clip(0.8 * flow, -1, 1)),
                trade_intensity_1s=float(50 + 10 * abs(pressure)),
                trade_intensity_5s=float(45 + 8 * abs(pressure)),
                signed_trade_contracts_1s=float(200 * flow),
                signed_trade_contracts_5s=float(700 * flow),
                book_updates=i + 1,
                trade_updates=(i + 1) * 3,
            )
        )
    return frames


def test_ws_challenger_builds_one_second_targets_without_authority():
    frames = _frames()
    rows = build_rows(frames, horizon_steps=4)
    assert len(rows) == len(frames) - 4
    model = WsFastExecutionModel(minimum_rows=240, heads=5)
    report = model.fit_and_evaluate(
        rows,
        symbol="ETHUSDT",
        frames=len(frames),
        horizon_ms=1000,
    )
    assert report.symbol == "ETHUSDT"
    assert len(report.targets) == 3
    assert all(metric.evaluation_samples >= 40 for metric in report.targets)
    assert report.paper_trading_authority is False
    assert report.live_trading_authority is False


def test_ws_challenger_rejects_small_sample():
    rows = build_rows(_frames(100), horizon_steps=4)
    model = WsFastExecutionModel(minimum_rows=240)
    try:
        model.fit_and_evaluate(rows, symbol="BTCUSDT", frames=100)
    except ValueError as exc:
        assert "insufficient websocket challenger rows" in str(exc)
    else:
        raise AssertionError("small sample must fail closed")
