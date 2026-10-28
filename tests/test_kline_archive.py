from datetime import UTC, date, datetime
import csv
import gzip

from triconsensus.kline_archive import (
    BybitKlineArchiveClient,
    archive_coverage_stats,
    parse_archive_datetime,
    parse_mt4_kline_archive,
)


def test_mt4_monthly_url_is_deterministic():
    client = BybitKlineArchiveClient()
    assert client.monthly_url("BTCUSDT", 2026, 8).endswith(
        "/BTCUSDT/2026/BTCUSDT_15_2026-08-01_2026-08-31.csv.gz"
    )


def test_archive_datetime_supports_common_formats():
    a = parse_archive_datetime("2026-08-11 12:15:00")
    b = parse_archive_datetime("2026.08.11 12:15")
    assert a == datetime(2026, 8, 11, 12, 15, tzinfo=UTC)
    assert b == a


def test_mt4_archive_parser_reads_headerless_ohlcv(tmp_path):
    path = tmp_path / "sample.csv.gz"
    rows = [
        ["2026-08-11 00:00:00", "100", "102", "99", "101", "12"],
        ["2026-08-11 00:15:00", "101", "103", "100", "102", "15"],
    ]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)

    candles = parse_mt4_kline_archive(path)
    assert len(candles) == 2
    assert candles[0]["open"] == 100
    assert candles[0]["close"] == 101
    assert candles[1]["volume"] == 15
    assert candles[0]["start_ms"] < candles[1]["start_ms"]


def test_archive_coverage_stats_detects_missing_intervals():
    candles = [
        {"start_ms": 0},
        {"start_ms": 900_000},
        {"start_ms": 2_700_000},
    ]
    stats = archive_coverage_stats(
        candles,
        requested_days=1,
    )
    assert stats["observed_candles"] == 3
    assert stats["ideal_candles"] == 96
    assert stats["gap_count"] == 1
    assert stats["largest_gap_ms"] == 1_800_000
