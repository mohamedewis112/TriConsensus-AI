from __future__ import annotations

import asyncio
import csv
from datetime import UTC, date, datetime, timedelta
import gzip
from pathlib import Path
import tempfile

import httpx


class PublicArchiveFailure(RuntimeError):
    pass


def aggregate_trade_archive(
    path: Path,
    *,
    interval_seconds: int = 15 * 60,
) -> list[dict]:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    buckets: dict[int, dict] = {}
    with gzip.open(path, mode="rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "price", "size"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise PublicArchiveFailure("unexpected Bybit archive columns")

        for row in reader:
            try:
                timestamp = float(row["timestamp"])
                price = float(row["price"])
                size = float(row["size"])
            except (TypeError, ValueError):
                continue
            if timestamp <= 0 or price <= 0 or size <= 0:
                continue

            # Bybit futures trade archive uses Unix seconds, often fractional.
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0

            bucket_s = int(timestamp // interval_seconds) * interval_seconds
            bucket = buckets.get(bucket_s)
            if bucket is None:
                buckets[bucket_s] = {
                    "start_ms": bucket_s * 1000,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": size,
                    "turnover": price * size,
                }
            else:
                bucket["high"] = max(float(bucket["high"]), price)
                bucket["low"] = min(float(bucket["low"]), price)
                bucket["close"] = price
                bucket["volume"] = float(bucket["volume"]) + size
                bucket["turnover"] = float(bucket["turnover"]) + (price * size)

    candles = [buckets[key] for key in sorted(buckets)]
    if not candles:
        raise PublicArchiveFailure("archive contained no valid trades")
    return candles


class BybitPublicArchiveClient:
    def __init__(self, base_url: str = "https://public.bybit.com/trading") -> None:
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("V1 supports BTCUSDT and ETHUSDT only")

    def url_for(self, symbol: str, day: date) -> str:
        self._validate_symbol(symbol)
        stamp = day.isoformat()
        return f"{self.base_url}/{symbol}/{symbol}{stamp}.csv.gz"

    async def daily_15m(self, symbol: str, day: date) -> list[dict]:
        url = self.url_for(symbol, day)
        temp_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                prefix=f"{symbol}-{day.isoformat()}-",
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
                            f"archive missing for {symbol} {day.isoformat()}"
                        )
                    response.raise_for_status()
                    with temp_path.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            handle.write(chunk)

            return aggregate_trade_archive(temp_path)
        except (httpx.HTTPError, OSError) as exc:
            raise PublicArchiveFailure(
                f"archive download failed for {symbol} {day.isoformat()}: "
                f"{type(exc).__name__}"
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
        if not 7 <= days <= 90:
            raise ValueError("archive training days must be between 7 and 90")

        # Use T-2 by default to avoid depending on an archive that is still publishing.
        final_day = end_day or (datetime.now(UTC).date() - timedelta(days=2))
        first_day = final_day - timedelta(days=days - 1)

        days_to_fetch: list[date] = []
        current = first_day
        while current <= final_day:
            days_to_fetch.append(current)
            current += timedelta(days=1)

        semaphore = asyncio.Semaphore(6)

        async def fetch_day(day: date) -> list[dict]:
            async with semaphore:
                return await self.daily_15m(symbol, day)

        daily_results = await asyncio.gather(
            *(fetch_day(day) for day in days_to_fetch)
        )

        candles_by_start: dict[int, dict] = {}
        for day_candles in daily_results:
            for candle in day_candles:
                candles_by_start[int(candle["start_ms"])] = candle

        candles = [candles_by_start[key] for key in sorted(candles_by_start)]
        expected_minimum = days * 90
        if len(candles) < expected_minimum:
            raise PublicArchiveFailure(
                f"archive coverage too sparse: {len(candles)} candles "
                f"for {days} requested days"
            )
        return candles
