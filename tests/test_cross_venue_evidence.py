from dataclasses import dataclass

import numpy as np
import pandas as pd

from triconsensus.binance_vision import FuturesMetricRow
from triconsensus.cross_venue_evidence import (
    analyze_cross_venue,
    build_cross_venue_frame,
    metrics_to_15m_frame,
)


def metric(ts, symbol="BTCUSDT", oi=1000, oi_value=100000, g=1.1, ta=1.2, tp=1.3, taker=0.9):
    return FuturesMetricRow(
        timestamp_ms=ts,
        symbol=symbol,
        sum_open_interest=float(oi),
        sum_open_interest_value=float(oi_value),
        count_toptrader_long_short_ratio=float(ta),
        sum_toptrader_long_short_ratio=float(tp),
        count_long_short_ratio=float(g),
        sum_taker_long_short_vol_ratio=float(taker),
    )


def test_metrics_bucket_end_labels_into_15m_candle():
    base = 1_699_999_200_000
    rows = [
        metric(base + 5 * 60_000, oi=100),
        metric(base + 10 * 60_000, oi=110),
        metric(base + 15 * 60_000, oi=120),
    ]
    frame = metrics_to_15m_frame(rows)
    assert len(frame) == 1
    assert frame.iloc[0]["sum_open_interest"] == 120
    assert frame.iloc[0]["metric_rows"] == 3


def test_cross_venue_frame_builds_features_and_coverage():
    base = 1_699_999_200_000
    candles = []
    metrics = []
    for i in range(180):
        ts = base + i * 900_000
        price = 100 + i * 0.1
        candles.append(
            {
                "start_ms": ts,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 10,
                "turnover": 1000,
            }
        )
        # Three 5m end-labelled rows per 15m candle.
        for m in (5, 10, 15):
            metrics.append(
                metric(
                    ts + m * 60_000,
                    oi=1000 + i * 2 + m,
                    oi_value=100000 + i * 100 + m,
                    g=1.05 + i * 1e-4,
                    ta=1.1 + i * 1e-4,
                    tp=1.2 + i * 1e-4,
                    taker=0.95 + i * 1e-4,
                )
            )

    frame, coverage = build_cross_venue_frame(
        candles=candles,
        metrics=metrics,
    )
    assert coverage["fully_matched_ratio"] == 1.0
    assert pd.isna(frame.iloc[0]["sum_open_interest"])
    assert frame.iloc[1]["sum_open_interest"] == 1015.0
    assert "oi_change_1h" in frame.columns
    assert "top_vs_global_positioning" in frame.columns
    assert "taker_long_short_log" in frame.columns


def test_cross_venue_analysis_can_detect_synthetic_evidence():
    n = 1200
    driver = np.linspace(-1.0, 1.0, n)
    frame = pd.DataFrame()
    for name in (
        "price_return_1h",
        "price_return_4h",
        "oi_change_1h",
        "oi_change_4h",
        "oi_value_change_1h",
        "oi_value_change_4h",
        "global_long_short_log",
        "global_long_short_change_1h",
        "top_account_long_short_log",
        "top_position_long_short_log",
        "top_vs_global_positioning",
        "taker_long_short_log",
        "taker_long_short_change_1h",
        "price_oi_interaction_1h",
        "price_oi_interaction_4h",
    ):
        frame[name] = driver if name == "oi_change_1h" else np.sin(np.arange(n) / 9)

    for horizon in (4, 12, 24, 48):
        frame[f"future_return_{horizon}"] = driver * 0.01

    report = analyze_cross_venue(frame)
    qualified = {
        (row["feature"], row["horizon"])
        for row in report["qualified"]
    }
    assert ("oi_change_1h", 4) in qualified
