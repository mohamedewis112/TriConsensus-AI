import asyncio

import pandas as pd
import pytest

from triconsensus.derivatives_history import (
    BybitDerivativesHistoryClient,
    DerivativesHistory,
    build_derivatives_feature_frame,
)


class FakePublicClient:
    def _validate_symbol(self, symbol):
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("bad symbol")

    def __init__(self):
        self.calls = []

    async def _get(self, path, params):
        self.calls.append((path, dict(params)))
        if path == "/v5/market/open-interest":
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "timestamp": "1800000",
                            "openInterest": "120",
                            "singleOpenInterest": "60",
                        },
                        {
                            "timestamp": "900000",
                            "openInterest": "100",
                            "singleOpenInterest": "50",
                        },
                    ],
                    "nextPageCursor": "",
                },
            }
        if path == "/v5/market/funding/history":
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "fundingRateTimestamp": "1800000",
                            "fundingRate": "0.0002",
                        },
                        {
                            "fundingRateTimestamp": "900000",
                            "fundingRate": "0.0001",
                        },
                    ]
                },
            }
        if path in {
            "/v5/market/mark-price-kline",
            "/v5/market/index-price-kline",
            "/v5/market/premium-index-price-kline",
        }:
            base = {
                "/v5/market/mark-price-kline": 101.0,
                "/v5/market/index-price-kline": 100.0,
                "/v5/market/premium-index-price-kline": 0.01,
            }[path]
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        ["1800000", str(base), str(base), str(base), str(base)],
                        ["900000", str(base), str(base), str(base), str(base)],
                    ]
                },
            }
        raise AssertionError(path)


def test_derivatives_history_clients_parse_and_sort():
    client = FakePublicClient()
    history = asyncio.run(
        BybitDerivativesHistoryClient(client).snapshot_history(
            "BTCUSDT",
            start_ms=900000,
            end_ms=1800000,
        )
    )
    assert [r["timestamp_ms"] for r in history.open_interest] == [900000, 1800000]
    assert [r["timestamp_ms"] for r in history.funding] == [900000, 1800000]
    assert [r["start_ms"] for r in history.mark_klines] == [900000, 1800000]
    assert history.open_interest[-1]["single_open_interest"] == 60.0
    assert any(path == "/v5/market/open-interest" for path, _ in client.calls)
    assert any(path == "/v5/market/funding/history" for path, _ in client.calls)


def test_derivatives_feature_frame_aligns_point_in_time_data():
    history = DerivativesHistory(
        open_interest=[
            {"timestamp_ms": 900000, "open_interest": 100.0},
            {"timestamp_ms": 1800000, "open_interest": 110.0},
            {"timestamp_ms": 2700000, "open_interest": 121.0},
        ],
        funding=[
            {"timestamp_ms": 900000, "funding_rate": 0.0001},
            {"timestamp_ms": 2700000, "funding_rate": 0.0002},
        ],
        mark_klines=[
            {"start_ms": 900000, "close": 101.0},
            {"start_ms": 1800000, "close": 102.0},
            {"start_ms": 2700000, "close": 103.0},
        ],
        index_klines=[
            {"start_ms": 900000, "close": 100.0},
            {"start_ms": 1800000, "close": 100.0},
            {"start_ms": 2700000, "close": 100.0},
        ],
        premium_klines=[
            {"start_ms": 900000, "close": 0.01},
            {"start_ms": 1800000, "close": 0.02},
            {"start_ms": 2700000, "close": 0.03},
        ],
    )
    candles = [
        {"start_ms": 900000, "close": 100.5},
        {"start_ms": 1800000, "close": 101.5},
        {"start_ms": 2700000, "close": 102.5},
    ]
    frame = build_derivatives_feature_frame(candles, history)

    assert isinstance(frame, pd.DataFrame)
    assert frame.loc[0, "funding_rate"] == 0.0001
    # Funding is point-in-time forward-filled, never backfilled from the future.
    assert frame.loc[1, "funding_rate"] == 0.0001
    assert frame.loc[2, "funding_rate"] == 0.0002
    assert frame.loc[1, "open_interest_return_1"] == pytest.approx(0.1)
    assert frame.loc[0, "mark_index_basis_bps"] == pytest.approx(100.0)


def test_reference_kline_request_uses_official_15m_interval():
    client = FakePublicClient()
    asyncio.run(
        BybitDerivativesHistoryClient(client).historical_reference_klines(
            "ETHUSDT",
            start_ms=900000,
            end_ms=1800000,
            kind="premium",
            timeframe="15m",
        )
    )
    call = next(
        params
        for path, params in client.calls
        if path == "/v5/market/premium-index-price-kline"
    )
    assert call["interval"] == "15"
    assert call["limit"] == 1000
