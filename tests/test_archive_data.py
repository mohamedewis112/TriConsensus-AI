import asyncio
import csv
from datetime import date
import gzip

import pytest

from triconsensus.archive_data import (
    BybitPublicArchiveClient,
    aggregate_trade_archive,
)


def test_public_archive_url_is_deterministic():
    client = BybitPublicArchiveClient()
    assert client.url_for("BTCUSDT", date(2026, 10, 26)).endswith(
        "/BTCUSDT/BTCUSDT2026-10-26.csv.gz"
    )


def test_trade_archive_aggregates_into_15m_ohlcv(tmp_path):
    path = tmp_path / "sample.csv.gz"
    rows = [
        {
            "timestamp": "1792963201.0",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "2",
            "price": "100",
        },
        {
            "timestamp": "1792963500.0",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "size": "1",
            "price": "103",
        },
        {
            "timestamp": "1792964101.0",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "4",
            "price": "101",
        },
    ]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["timestamp", "symbol", "side", "size", "price"],
        )
        writer.writeheader()
        writer.writerows(rows)

    candles = aggregate_trade_archive(path)
    assert len(candles) == 2
    first = candles[0]
    assert first["open"] == 100
    assert first["high"] == 103
    assert first["low"] == 100
    assert first["close"] == 103
    assert first["volume"] == 3
    assert first["turnover"] == pytest.approx(303)


def test_historical_archive_merges_parallel_days_in_time_order():
    class FakeArchiveClient(BybitPublicArchiveClient):
        async def daily_15m(self, symbol, day):
            base = int(day.strftime("%d"))
            return [
                {
                    "start_ms": base * 1000,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                    "turnover": 1.0,
                }
            ]

    client = FakeArchiveClient()
    candles = asyncio.run(
        client.historical_15m(
            "BTCUSDT",
            days=7,
            end_day=date(2026, 10, 7),
        )
    )
    assert [row["start_ms"] for row in candles] == sorted(
        row["start_ms"] for row in candles
    )
