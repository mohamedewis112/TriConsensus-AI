from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, time, timedelta
import json

from .config import settings
from .derivatives_data import (
    AccountRatioPoint,
    BybitDerivativesClient,
    FundingPoint,
    OpenInterestPoint,
    align_derivatives_to_15m,
)


INTERVAL_MS = 15 * 60 * 1000


def window_ms(*, days: int, end_day: date) -> tuple[int, int]:
    if days < 1:
        raise ValueError("days must be positive")
    first_day = end_day - timedelta(days=days - 1)
    start = datetime.combine(first_day, time.min, tzinfo=UTC)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000) - INTERVAL_MS


def expected_grid(*, start_ms: int, end_ms: int) -> list[int]:
    if start_ms <= 0 or end_ms < start_ms:
        raise ValueError("invalid grid range")
    return list(range(start_ms, end_ms + 1, INTERVAL_MS))


def _coverage(timestamps: set[int], grid: list[int]) -> float:
    if not grid:
        return 0.0
    return sum(1 for ts in grid if ts in timestamps) / len(grid)


def _gap_stats(timestamps: list[int]) -> dict:
    if len(timestamps) < 2:
        return {
            "median_gap_ms": None,
            "max_gap_ms": None,
        }
    ordered = sorted(timestamps)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    gaps_sorted = sorted(gaps)
    middle = len(gaps_sorted) // 2
    if len(gaps_sorted) % 2:
        median = gaps_sorted[middle]
    else:
        median = (gaps_sorted[middle - 1] + gaps_sorted[middle]) / 2
    return {
        "median_gap_ms": int(median),
        "max_gap_ms": int(max(gaps)),
    }


def build_probe_report(
    *,
    symbol: str,
    start_ms: int,
    end_ms: int,
    funding: list[FundingPoint],
    open_interest: list[OpenInterestPoint],
    account_ratio: list[AccountRatioPoint],
) -> dict:
    grid = expected_grid(start_ms=start_ms, end_ms=end_ms)
    grid_set = set(grid)

    oi_times = {row.timestamp_ms for row in open_interest}
    ratio_times = {row.timestamp_ms for row in account_ratio}

    aligned = align_derivatives_to_15m(
        timestamps_ms=grid,
        funding=funding,
        open_interest=open_interest,
        account_ratio=account_ratio,
    )
    complete = [
        row
        for row in aligned
        if row["funding_rate"] is not None
        and row["open_interest"] is not None
        and row["long_short_ratio"] is not None
    ]

    funding_in_window = [
        row
        for row in funding
        if start_ms <= row.timestamp_ms <= end_ms
    ]
    funding_gaps = _gap_stats([row.timestamp_ms for row in funding_in_window])

    oi_coverage = _coverage(oi_times, grid)
    ratio_coverage = _coverage(ratio_times, grid)
    complete_coverage = len(complete) / len(grid) if grid else 0.0

    funding_ok = (
        len(funding_in_window) >= 10
        and funding_gaps["median_gap_ms"] is not None
        and funding_gaps["median_gap_ms"] <= 12 * 60 * 60 * 1000
        and funding_gaps["max_gap_ms"] is not None
        and funding_gaps["max_gap_ms"] <= 24 * 60 * 60 * 1000
    )

    quality = {
        "oi_coverage_ratio": oi_coverage,
        "account_ratio_coverage_ratio": ratio_coverage,
        "aligned_complete_coverage_ratio": complete_coverage,
        "funding_points_in_window": len(funding_in_window),
        "funding_median_gap_ms": funding_gaps["median_gap_ms"],
        "funding_max_gap_ms": funding_gaps["max_gap_ms"],
    }
    passed = (
        oi_coverage >= 0.95
        and ratio_coverage >= 0.95
        and complete_coverage >= 0.95
        and funding_ok
    )

    return {
        "experiment": "V5-DERIVATIVES-DATA-PROBE",
        "schema_version": 1,
        "symbol": symbol,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "expected_15m_points": len(grid),
        "open_interest_points": sum(
            1 for row in open_interest if row.timestamp_ms in grid_set
        ),
        "account_ratio_points": sum(
            1 for row in account_ratio if row.timestamp_ms in grid_set
        ),
        "funding_points_total_with_warmup": len(funding),
        "complete_aligned_points": len(complete),
        "quality": quality,
        "quality_gate_passed": passed,
        "default_action": "NO_TRADE",
    }


async def run_probe(
    *,
    symbol: str,
    days: int,
    end_day: date,
) -> dict:
    start_ms, end_ms = window_ms(days=days, end_day=end_day)
    warmup_start = start_ms - (24 * 60 * 60 * 1000)
    client = BybitDerivativesClient(settings)

    funding, open_interest, account_ratio = await asyncio.gather(
        client.funding_history(
            symbol,
            start_ms=warmup_start,
            end_ms=end_ms,
        ),
        client.open_interest_history(
            symbol,
            start_ms=start_ms,
            end_ms=end_ms,
            interval="15min",
        ),
        client.account_ratio_history(
            symbol,
            start_ms=start_ms,
            end_ms=end_ms,
            period="15min",
        ),
    )
    return build_probe_report(
        symbol=symbol,
        start_ms=start_ms,
        end_ms=end_ms,
        funding=funding,
        open_interest=open_interest,
        account_ratio=account_ratio,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TriConsensus V5 derivatives historical data probe"
    )
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()

    report = asyncio.run(
        run_probe(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["quality_gate_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
