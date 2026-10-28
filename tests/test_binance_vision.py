import asyncio
import io
import zipfile

from triconsensus.binance_vision import (
    BinanceVisionMetricsClient,
    FuturesMetricRow,
    parse_metrics_zip,
)


def _zip_bytes(csv_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample.csv", csv_text)
    return buffer.getvalue()


def test_parse_metrics_zip_reads_expected_columns():
    payload = _zip_bytes(
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        "2024-11-30 00:05:00,BTCUSDT,100,10000,1.1,1.2,1.05,0.9\n"
    )
    rows = parse_metrics_zip(payload, symbol="BTCUSDT")
    assert rows == [
        FuturesMetricRow(
            timestamp_ms=1732925100000,
            symbol="BTCUSDT",
            sum_open_interest=100.0,
            sum_open_interest_value=10000.0,
            count_toptrader_long_short_ratio=1.1,
            sum_toptrader_long_short_ratio=1.2,
            count_long_short_ratio=1.05,
            sum_taker_long_short_vol_ratio=0.9,
        )
    ]


def test_daily_url_matches_binance_vision_layout():
    from datetime import date

    client = BinanceVisionMetricsClient()
    assert client.daily_url("ETHUSDT", date(2024, 11, 30)).endswith(
        "/data/futures/um/daily/metrics/ETHUSDT/"
        "ETHUSDT-metrics-2024-11-30.zip"
    )


def test_historical_metrics_allows_small_missing_day_fraction():
    from datetime import date

    class FakeClient(BinanceVisionMetricsClient):
        async def daily_metrics(self, symbol, day):
            if day.day == 2:
                return []
            return [
                FuturesMetricRow(
                    timestamp_ms=day.day * 1000,
                    symbol=symbol,
                    sum_open_interest=100,
                    sum_open_interest_value=1000,
                    count_toptrader_long_short_ratio=1,
                    sum_toptrader_long_short_ratio=1,
                    count_long_short_ratio=1,
                    sum_taker_long_short_vol_ratio=1,
                )
            ]

    rows = asyncio.run(
        FakeClient().historical_metrics(
            "BTCUSDT",
            days=10,
            end_day=date(2024, 1, 10),
        )
    )
    assert len(rows) == 9



def test_parse_metrics_zip_normalizes_2026_start_labels_by_archive_day():
    from datetime import date

    payload = _zip_bytes(
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        "2026-06-25 00:00:00,BTCUSDT,100,10000,1.1,1.2,1.05,0.9\n"
    )
    old_file_row = parse_metrics_zip(
        payload,
        symbol="BTCUSDT",
        archive_day=date(2026, 6, 24),
    )[0]
    new_file_row = parse_metrics_zip(
        payload,
        symbol="BTCUSDT",
        archive_day=date(2026, 6, 25),
    )[0]
    assert new_file_row.timestamp_ms - old_file_row.timestamp_ms == 5 * 60 * 1000
