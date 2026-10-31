import pytest

from triconsensus.event_study import (
    analyze_event_study,
    forward_path_metrics,
)


def candles():
    rows = []
    for i in range(80):
        price = 100 + i
        rows.append(
            {
                "start_ms": i * 900_000,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 100,
                "turnover": price * 100,
            }
        )
    return rows


def test_forward_path_metrics_long_and_short():
    rows = candles()
    long = forward_path_metrics(
        rows,
        index=10,
        side=1,
        horizon=4,
        round_trip_cost=0.0,
    )
    short = forward_path_metrics(
        rows,
        index=10,
        side=-1,
        horizon=4,
        round_trip_cost=0.0,
    )
    assert long["gross_return"] > 0
    assert short["gross_return"] < 0
    assert long["mfe"] > 0
    assert long["mae"] > -0.1
    assert short["mae"] < 0


def _events(value: float, n: int = 120):
    rows = []
    for i in range(n):
        rows.append(
            {
                "timestamp_ms": i * 900_000,
                "entry_index": i,
                "setup": "trend_continuation",
                "side": "LONG",
                "regime": "trend_up|mid_vol|momentum_up",
                "trend": "trend_up",
                "volatility": "mid_vol",
                "momentum": "momentum_up",
                "horizon": 12,
                "gross_return": value + 0.0015,
                "net_return": value,
                "mfe": max(value + 0.002, 0.002),
                "mae": -0.001,
            }
        )
    return rows


def test_event_study_can_qualify_stable_entry_edge():
    report = analyze_event_study(_events(0.001))
    assert report["entry_edge_passed"] is True
    assert report["qualified_primary_count"] == 1
    row = report["qualified_primary"][0]
    assert row["regime"] == "trend_continuation|H12"
    assert row["side"] == "LONG"
    assert row["mean_mfe"] > 0
    assert row["mean_mae"] < 0


def test_event_study_is_fail_closed_for_negative_edge():
    report = analyze_event_study(_events(-0.001))
    assert report["entry_edge_passed"] is False
    assert report["qualified_primary_count"] == 0


def test_event_study_empty_is_no_trade():
    report = analyze_event_study([])
    assert report["entry_edge_passed"] is False
    assert report["qualified_primary"] == []
