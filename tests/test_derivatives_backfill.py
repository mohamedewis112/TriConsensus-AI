from datetime import UTC, date, datetime

from triconsensus.derivatives_backfill import (
    _coverage,
    _day_bounds,
    _timeline,
    build_backfill_payload,
)
from triconsensus.derivatives_data import (
    AccountRatioPoint,
    FundingPoint,
    OpenInterestPoint,
)


def test_day_bounds_are_full_utc_days():
    start, end = _day_bounds(date(2026, 10, 1), date(2026, 10, 1))
    assert start == int(datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000)
    assert end - start + 1 == 86_400_000


def test_timeline_is_15m_and_inclusive():
    start = 900_000
    end = 3_600_000
    assert _timeline(start, end) == [900_000, 1_800_000, 2_700_000, 3_600_000]


def test_coverage_requires_real_derivatives_completeness():
    rows = [
        {
            "funding_rate": 0.0001,
            "open_interest": 10.0,
            "long_short_ratio": 1.1,
        }
        for _ in range(100)
    ]
    quality = _coverage(rows)
    assert quality["qualification_ready"] is True

    rows[0]["open_interest"] = None
    rows[1]["long_short_ratio"] = None
    assert _coverage(rows)["qualification_ready"] is True

    for row in rows[:20]:
        row["open_interest"] = None
    assert _coverage(rows)["qualification_ready"] is False


def test_backfill_payload_aligns_without_inventing_oi_or_ratio():
    payload = build_backfill_payload(
        symbol="BTCUSDT",
        start_ms=900_000,
        end_ms=2_700_000,
        funding=[FundingPoint("BTCUSDT", 900_000, 0.0001)],
        open_interest=[OpenInterestPoint("BTCUSDT", 1_800_000, 10.0)],
        account_ratio=[
            AccountRatioPoint("BTCUSDT", 2_700_000, 0.55, 0.45, 0.55 / 0.45)
        ],
    )
    assert payload["aligned_15m"][0]["funding_rate"] == 0.0001
    assert payload["aligned_15m"][0]["open_interest"] is None
    assert payload["aligned_15m"][1]["open_interest"] == 10.0
    assert payload["aligned_15m"][2]["long_short_ratio"] > 1.0
