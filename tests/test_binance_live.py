import asyncio
import math

import pytest

from triconsensus.binance_live import (
    BinanceGlobalRatioClient,
    BinanceLiveContextFailure,
    GlobalLongShortPoint,
    normalize_global_ratio_timestamp,
)


def point(ts, ratio):
    return GlobalLongShortPoint(
        timestamp_ms=ts,
        ratio=ratio,
        long_account=0.5,
        short_account=0.5,
    )


def contiguous_points(base, ratios):
    return [
        point(base + i * 900_000, ratio)
        for i, ratio in enumerate(ratios)
    ]


def test_live_signal_uses_full_15m_lag_and_one_hour_change():
    base = 1_700_000_000_000
    client = BinanceGlobalRatioClient(
        context_lag_minutes=15,
        max_context_age_minutes=45,
    )
    points = contiguous_points(
        base,
        [1.00, 1.01, 1.02, 1.03, 0.96, 0.94],
    )
    now_ms = points[-1].timestamp_ms + 15 * 60_000
    result = client.signal_from_points(
        "BTCUSDT",
        points,
        low_threshold=-0.03,
        high_threshold=0.03,
        now_ms=now_ms,
    )
    expected = math.log(0.94) - math.log(1.01)
    assert result.feature_value == pytest.approx(expected)
    assert result.signal == "LONG"
    assert result.context_age_ms == 15 * 60_000


def test_live_signal_short_for_upper_decile():
    base = 1_700_000_000_000
    client = BinanceGlobalRatioClient()
    points = contiguous_points(
        base,
        [1.00, 0.99, 1.00, 1.01, 1.05, 1.08],
    )
    now_ms = points[-1].timestamp_ms + 15 * 60_000
    result = client.signal_from_points(
        "ETHUSDT",
        points,
        low_threshold=-0.03,
        high_threshold=0.03,
        now_ms=now_ms,
    )
    assert result.signal == "SHORT"


def test_live_signal_no_trade_inside_deciles():
    base = 1_700_000_000_000
    client = BinanceGlobalRatioClient()
    points = contiguous_points(
        base,
        [1.00, 1.00, 1.00, 1.00, 1.01, 1.01],
    )
    now_ms = points[-1].timestamp_ms + 15 * 60_000
    result = client.signal_from_points(
        "BTCUSDT",
        points,
        low_threshold=-0.03,
        high_threshold=0.03,
        now_ms=now_ms,
    )
    assert result.signal == "NO_TRADE"


def test_live_signal_rejects_stale_context():
    base = 1_700_000_000_000
    client = BinanceGlobalRatioClient(max_context_age_minutes=45)
    points = contiguous_points(base, [1.0] * 6)
    with pytest.raises(BinanceLiveContextFailure, match="stale"):
        client.signal_from_points(
            "BTCUSDT",
            points,
            low_threshold=-0.03,
            high_threshold=0.03,
            now_ms=points[-1].timestamp_ms + 46 * 60_000,
        )


def test_live_signal_rejects_non_contiguous_ratio_series():
    base = 1_700_000_000_000
    points = contiguous_points(base, [1.0] * 6)
    points[-2] = point(points[-2].timestamp_ms - 60_000, 1.0)
    client = BinanceGlobalRatioClient()
    with pytest.raises(BinanceLiveContextFailure):
        client.signal_from_points(
            "BTCUSDT",
            points,
            low_threshold=-0.03,
            high_threshold=0.03,
            now_ms=points[-1].timestamp_ms + 15 * 60_000,
        )



def test_live_timestamp_normalizes_post_2026_start_labels():
    before = 1782345599999
    cutoff = 1782345600000
    assert normalize_global_ratio_timestamp(before) == before
    assert normalize_global_ratio_timestamp(cutoff) == cutoff + 5 * 60_000
