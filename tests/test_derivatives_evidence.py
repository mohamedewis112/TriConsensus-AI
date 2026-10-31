from dataclasses import dataclass

import numpy as np
import pandas as pd

from triconsensus.derivatives_evidence import (
    build_context_frame,
    research_window_ms,
    score_feature,
)


@dataclass(frozen=True)
class Point:
    timestamp_ms: int
    value: float


@dataclass(frozen=True)
class RatioPoint:
    timestamp_ms: int
    buy_ratio: float
    sell_ratio: float


def test_derivatives_evidence_qualifies_stable_monotonic_feature():
    n = 1200
    feature = np.linspace(-1.0, 1.0, n)
    target = 0.01 * feature
    frame = pd.DataFrame(
        {
            "x": feature,
            "future_return_12": target,
        }
    )
    score = score_feature(
        frame,
        feature="x",
        horizon=12,
        round_trip_cost=0.0001,
    )
    assert score.qualified is True
    assert score.same_sign_folds == 4
    assert score.mean_rank_ic > 0.9
    assert score.mean_cost_adjusted_decile_spread > 0


def test_derivatives_evidence_rejects_unstable_feature():
    n = 1200
    feature = np.tile(np.linspace(-1.0, 1.0, 300), 4)
    target = np.concatenate(
        [
            feature[:300],
            -feature[300:600],
            feature[600:900],
            -feature[900:1200],
        ]
    ) * 0.01
    frame = pd.DataFrame(
        {
            "x": feature,
            "future_return_12": target,
        }
    )
    score = score_feature(
        frame,
        feature="x",
        horizon=12,
        round_trip_cost=0.0001,
    )
    assert score.qualified is False
    assert score.same_sign_folds < 3


def test_context_frame_aligns_sparse_funding_asof():
    candles = []
    oi = []
    ratios = []
    premium = []
    for i in range(160):
        ts = 1_700_000_000_000 + i * 900_000
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
        oi.append(Point(ts, 1000 + i))
        ratios.append(RatioPoint(ts, 0.52, 0.48))
        premium.append(Point(ts, 0.001 + i * 1e-7))

    funding = [
        Point(candles[0]["start_ms"], 0.0001),
        Point(candles[32]["start_ms"], 0.0002),
        Point(candles[64]["start_ms"], -0.0001),
    ]
    frame, coverage = build_context_frame(
        candles=candles,
        context={
            "open_interest": oi,
            "account_ratio": ratios,
            "funding": funding,
            "premium_index": premium,
        },
    )
    assert coverage["open_interest"] == 1.0
    assert coverage["account_ratio"] == 1.0
    assert coverage["premium_index"] == 1.0
    assert coverage["funding_asof"] == 1.0
    assert frame.iloc[40]["funding_rate"] == 0.0002


def test_research_window_is_exact():
    from datetime import UTC, date, datetime

    start_ms, end_ms = research_window_ms(
        days=45,
        end_day=date(2026, 8, 11),
    )
    start = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
    end = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    assert start.isoformat() == "2026-06-28T00:00:00+00:00"
    assert end.isoformat() == "2026-08-11T23:59:59.999000+00:00"
