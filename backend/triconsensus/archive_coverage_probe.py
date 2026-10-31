from __future__ import annotations

import argparse
import asyncio
import calendar
from datetime import date
import json

from .kline_archive import BybitKlineArchiveClient


async def probe(symbol: str, year: int, start_month: int, end_month: int) -> dict:
    client = BybitKlineArchiveClient()
    rows = []
    for month in range(start_month, end_month + 1):
        candles = await client.monthly_15m(symbol, year, month)
        expected = calendar.monthrange(year, month)[1] * 96
        observed = len(candles)
        starts = sorted(int(row["start_ms"]) for row in candles)
        gap_count = 0
        largest_gap_ms = 900_000
        for left, right in zip(starts, starts[1:]):
            diff = right - left
            if diff != 900_000:
                gap_count += 1
                largest_gap_ms = max(largest_gap_ms, diff)
        rows.append({
            "year": year,
            "month": month,
            "observed": observed,
            "expected": expected,
            "coverage_ratio": observed / expected if expected else 0.0,
            "gap_count": gap_count,
            "largest_gap_ms": largest_gap_ms,
        })
    return {"symbol": symbol, "year": year, "months": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--start-month", type=int, default=1)
    parser.add_argument("--end-month", type=int, default=12)
    args = parser.parse_args()
    result = asyncio.run(
        probe(args.symbol, args.year, args.start_month, args.end_month)
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
