from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path

from .config import Settings
from .derivatives_data import (
    AccountRatioPoint,
    BybitDerivativesClient,
    FundingPoint,
    OpenInterestPoint,
    align_derivatives_to_15m,
)


INTERVAL_MS = 15 * 60 * 1000


def _day_bounds(start_day: date, end_day: date) -> tuple[int, int]:
    if end_day < start_day:
        raise ValueError("end date must not precede start date")
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)
    end_exclusive = datetime.combine(
        end_day + timedelta(days=1),
        datetime.min.time(),
        tzinfo=UTC,
    )
    return int(start.timestamp() * 1000), int(end_exclusive.timestamp() * 1000) - 1


def _timeline(start_ms: int, end_ms: int) -> list[int]:
    first = ((start_ms + INTERVAL_MS - 1) // INTERVAL_MS) * INTERVAL_MS
    last = (end_ms // INTERVAL_MS) * INTERVAL_MS
    if last < first:
        return []
    return list(range(first, last + 1, INTERVAL_MS))


def _coverage(aligned: list[dict]) -> dict:
    total = len(aligned)
    if total == 0:
        return {
            "rows": 0,
            "funding_coverage": 0.0,
            "open_interest_coverage": 0.0,
            "account_ratio_coverage": 0.0,
            "complete_row_coverage": 0.0,
            "qualification_ready": False,
        }

    funding = sum(row["funding_rate"] is not None for row in aligned)
    oi = sum(row["open_interest"] is not None for row in aligned)
    ratio = sum(row["long_short_ratio"] is not None for row in aligned)
    complete = sum(
        row["funding_rate"] is not None
        and row["open_interest"] is not None
        and row["long_short_ratio"] is not None
        for row in aligned
    )
    quality = {
        "rows": total,
        "funding_coverage": funding / total,
        "open_interest_coverage": oi / total,
        "account_ratio_coverage": ratio / total,
        "complete_row_coverage": complete / total,
    }
    quality["qualification_ready"] = (
        quality["funding_coverage"] >= 0.95
        and quality["open_interest_coverage"] >= 0.90
        and quality["account_ratio_coverage"] >= 0.90
        and quality["complete_row_coverage"] >= 0.85
    )
    return quality


def build_backfill_payload(
    *,
    symbol: str,
    start_ms: int,
    end_ms: int,
    funding: list[FundingPoint],
    open_interest: list[OpenInterestPoint],
    account_ratio: list[AccountRatioPoint],
) -> dict:
    timeline = _timeline(start_ms, end_ms)
    aligned = align_derivatives_to_15m(
        timestamps_ms=timeline,
        funding=funding,
        open_interest=open_interest,
        account_ratio=account_ratio,
    )
    quality = _coverage(aligned)
    return {
        "schema_version": 1,
        "source": "bybit_v5_public",
        "symbol": symbol,
        "interval": "15min",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "generated_at": datetime.now(UTC).isoformat(),
        "raw_counts": {
            "funding": len(funding),
            "open_interest": len(open_interest),
            "account_ratio": len(account_ratio),
        },
        "quality": quality,
        "funding": [asdict(row) for row in funding],
        "open_interest": [asdict(row) for row in open_interest],
        "account_ratio": [asdict(row) for row in account_ratio],
        "aligned_15m": aligned,
    }


async def backfill(
    *,
    symbol: str,
    start_day: date,
    end_day: date,
    output_dir: Path,
) -> dict:
    start_ms, end_ms = _day_bounds(start_day, end_day)
    client = BybitDerivativesClient(Settings())

    funding, open_interest, account_ratio = await asyncio.gather(
        client.funding_history(
            symbol,
            start_ms=start_ms,
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

    payload = build_backfill_payload(
        symbol=symbol,
        start_ms=start_ms,
        end_ms=end_ms,
        funding=funding,
        open_interest=open_interest,
        account_ratio=account_ratio,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "derivatives-backfill.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill TriConsensus V4.2 public Bybit derivatives evidence"
    )
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    result = asyncio.run(
        backfill(
            symbol=args.symbol,
            start_day=date.fromisoformat(args.start_date),
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
        )
    )
    summary = {
        "symbol": result["symbol"],
        "start_ms": result["start_ms"],
        "end_ms": result["end_ms"],
        "raw_counts": result["raw_counts"],
        "quality": result["quality"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
