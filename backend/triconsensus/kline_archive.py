from __future__ import annotations

import asyncio
import calendar
import csv
from datetime import UTC, date, datetime, timedelta
import gzip
from pathlib import Path
import tempfile

import httpx

from .archive_data import BybitPublicArchiveClient, PublicArchiveFailure


def parse_archive_datetime(value: str) -> datetime:
    text = value.strip()
    try:
        numeric = float(text)
    except ValueError:
        numeric = None

    if numeric is not None:
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=UTC)

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None

    if parsed is None:
        for fmt in (
            "%Y.%m.%d %H:%M",
            "%Y.%m.%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        raise ValueError(f"unsupported archive datetime: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_mt4_kline_archive(path: Path) -> list[dict]:
    candles: list[dict] = []
    with gzip.open(path, mode="rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 6:
                continue
            try:
                timestamp = parse_archive_datetime(row[0])
                open_price = float(row[1])
                high = float(row[2])
                low = float(row[3])
                close = float(row[4])
                volume = float(row[5])
            except (TypeError, ValueError):
                continue

            if min(open_price, high, low, close) <= 0 or volume < 0:
                continue

            candles.append(
                {
                    "start_ms": int(timestamp.timestamp() * 1000),
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "turnover": 0.0,
                }
            )

    candles.sort(key=lambda row: int(row["start_ms"]))
    if not candles:
        raise PublicArchiveFailure("MT4 kline archive contained no valid candles")
    return candles


def archive_coverage_stats(
    candles: list[dict],
    *,
    requested_days: int,
    interval_ms: int = 15 * 60 * 1000,
) -> dict:
    if requested_days <= 0 or interval_ms <= 0:
        raise ValueError("invalid coverage parameters")
    if not candles:
        return {
            "observed_candles": 0,
            "ideal_candles": requested_days * (86_400_000 // interval_ms),
            "coverage_ratio": 0.0,
            "gap_count": 0,
            "largest_gap_ms": 0,
        }

    starts = sorted(int(row["start_ms"]) for row in candles)
    diffs = [b - a for a, b in zip(starts, starts[1:])]
    gaps = [diff for diff in diffs if diff != interval_ms]
    ideal = requested_days * (86_400_000 // interval_ms)
    return {
        "observed_candles": len(starts),
        "ideal_candles": int(ideal),
        "coverage_ratio": len(starts) / ideal if ideal else 0.0,
        "gap_count": len(gaps),
        "largest_gap_ms": max(gaps) if gaps else interval_ms,
    }


class BybitKlineArchiveClient:
    def __init__(
        self,
        base_url: str = "https://public.bybit.com/kline_for_metatrader4",
        fallback: BybitPublicArchiveClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fallback = fallback or BybitPublicArchiveClient()

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("V4 supports BTCUSDT and ETHUSDT only")

    def monthly_url(self, symbol: str, year: int, month: int) -> str:
        self._validate_symbol(symbol)
        last_day = calendar.monthrange(year, month)[1]
        start = f"{year:04d}-{month:02d}-01"
        end = f"{year:04d}-{month:02d}-{last_day:02d}"
        name = f"{symbol}_15_{start}_{end}.csv.gz"
        return f"{self.base_url}/{symbol}/{year:04d}/{name}"

    async def monthly_15m(
        self,
        symbol: str,
        year: int,
        month: int,
    ) -> list[dict]:
        url = self.monthly_url(symbol, year, month)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f"{symbol}-{year:04d}-{month:02d}-",
                suffix=".csv.gz",
                delete=False,
            ) as temp:
                temp_path = Path(temp.name)

            timeout = httpx.Timeout(120.0, connect=20.0)
            headers = {"User-Agent": "TriConsensus-AI/0.1 research"}
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code == 404:
                        raise PublicArchiveFailure(
                            "MT4 kline archive missing for "
                            f"{symbol} {year:04d}-{month:02d}"
                        )
                    response.raise_for_status()
                    with temp_path.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            handle.write(chunk)

            return parse_mt4_kline_archive(temp_path)
        except (httpx.HTTPError, OSError) as exc:
            raise PublicArchiveFailure(
                "MT4 kline archive download failed for "
                f"{symbol} {year:04d}-{month:02d}: {type(exc).__name__}"
            ) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    async def historical_15m(
        self,
        symbol: str,
        *,
        days: int,
        end_day: date | None = None,
    ) -> list[dict]:
        self._validate_symbol(symbol)
        if not 7 <= days <= 365:
            raise ValueError("kline archive days must be between 7 and 365")

        final_day = end_day or (datetime.now(UTC).date() - timedelta(days=2))
        first_day = final_day - timedelta(days=days - 1)

        months: list[tuple[int, int]] = []
        cursor = date(first_day.year, first_day.month, 1)
        final_month = date(final_day.year, final_day.month, 1)
        while cursor <= final_month:
            months.append((cursor.year, cursor.month))
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)

        semaphore = asyncio.Semaphore(4)

        async def fetch_month(year: int, month: int) -> list[dict]:
            async with semaphore:
                return await self.monthly_15m(symbol, year, month)

        async def fetch_month_safe(
            year: int,
            month: int,
        ) -> tuple[list[dict], tuple[int, int] | None]:
            try:
                return await fetch_month(year, month), None
            except PublicArchiveFailure:
                return [], (year, month)

        monthly_results = await asyncio.gather(
            *(fetch_month_safe(year, month) for year, month in months)
        )
        candles_by_start: dict[int, dict] = {}
        start_ms = int(
            datetime.combine(
                first_day,
                datetime.min.time(),
                tzinfo=UTC,
            ).timestamp()
            * 1000
        )
        end_ms = int(
            datetime.combine(
                final_day + timedelta(days=1),
                datetime.min.time(),
                tzinfo=UTC,
            ).timestamp()
            * 1000
        )

        missing_months: list[tuple[int, int]] = []
        for month_rows, missing in monthly_results:
            if missing is not None:
                missing_months.append(missing)
                continue
            for candle in month_rows:
                timestamp = int(candle["start_ms"])
                if start_ms <= timestamp < end_ms:
                    candles_by_start[timestamp] = candle

        fallback_days: list[date] = []
        for year, month in missing_months:
            month_first = date(year, month, 1)
            month_last = date(year, month, calendar.monthrange(year, month)[1])
            cursor_day = max(first_day, month_first)
            final_missing_day = min(final_day, month_last)
            while cursor_day <= final_missing_day:
                fallback_days.append(cursor_day)
                cursor_day += timedelta(days=1)

        if fallback_days:
            daily_semaphore = asyncio.Semaphore(4)

            async def fetch_day(day: date) -> list[dict]:
                async with daily_semaphore:
                    return await self.fallback.daily_15m(symbol, day)

            daily_results = await asyncio.gather(
                *(fetch_day(day) for day in fallback_days)
            )
            for day_rows in daily_results:
                for candle in day_rows:
                    timestamp = int(candle["start_ms"])
                    if start_ms <= timestamp < end_ms:
                        candles_by_start[timestamp] = candle

        candles = [candles_by_start[key] for key in sorted(candles_by_start)]
        quality = archive_coverage_stats(candles, requested_days=days)
        if quality["coverage_ratio"] < 0.80:
            raise PublicArchiveFailure(
                "hybrid kline archive coverage too sparse: "
                f'{quality["coverage_ratio"]:.3f} for {days} requested days'
            )
        return candles
