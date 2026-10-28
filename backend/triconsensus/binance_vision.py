from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import io
import math
import zipfile

import httpx


UM_METRICS_START_LABEL_DAY = date(2026, 6, 25)
FIVE_MINUTES_MS = 5 * 60 * 1000


METRIC_COLUMNS = (
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


class BinanceVisionFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class FuturesMetricRow:
    timestamp_ms: int
    symbol: str
    sum_open_interest: float
    sum_open_interest_value: float
    count_toptrader_long_short_ratio: float
    sum_toptrader_long_short_ratio: float
    count_long_short_ratio: float
    sum_taker_long_short_vol_ratio: float


def parse_metrics_zip(
    payload: bytes,
    *,
    symbol: str,
    archive_day: date | None = None,
) -> list[FuturesMetricRow]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(names) != 1:
                raise BinanceVisionFailure("metrics zip must contain one CSV")
            with archive.open(names[0]) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                reader = csv.DictReader(text)
                rows = []
                for row in reader:
                    try:
                        parsed = datetime.strptime(
                            row["create_time"],
                            "%Y-%m-%d %H:%M:%S",
                        ).replace(tzinfo=UTC)
                        values = {
                            name: float(row[name])
                            for name in METRIC_COLUMNS
                        }
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not all(math.isfinite(value) for value in values.values()):
                        continue
                    timestamp_ms = int(parsed.timestamp() * 1000)
                    # Binance USD-M metrics changed from end-labelled to
                    # start-labelled five-minute periods on 2026-06-25.
                    # Normalize post-change archive rows to the actual period
                    # end/availability timestamp so old and new files share
                    # one time convention. Use archive_day, not parsed time,
                    # because the 2026-06-24 file also contains a 06-25 00:00
                    # old-convention row.
                    if (
                        archive_day is not None
                        and archive_day >= UM_METRICS_START_LABEL_DAY
                    ):
                        timestamp_ms += FIVE_MINUTES_MS

                    rows.append(
                        FuturesMetricRow(
                            timestamp_ms=timestamp_ms,
                            symbol=str(row.get("symbol") or symbol),
                            **values,
                        )
                    )
    except (zipfile.BadZipFile, OSError) as exc:
        raise BinanceVisionFailure("invalid metrics zip") from exc

    rows.sort(key=lambda item: item.timestamp_ms)
    if not rows:
        raise BinanceVisionFailure("metrics archive contained no valid rows")
    return rows


class BinanceVisionMetricsClient:
    def __init__(
        self,
        base_url: str = "https://data.binance.vision",
        *,
        concurrency: int = 4,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._semaphore = asyncio.Semaphore(concurrency)

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("supported symbols are BTCUSDT and ETHUSDT")

    def daily_url(self, symbol: str, day: date) -> str:
        self._validate_symbol(symbol)
        stamp = day.isoformat()
        return (
            f"{self.base_url}/data/futures/um/daily/metrics/"
            f"{symbol}/{symbol}-metrics-{stamp}.zip"
        )

    async def daily_metrics(
        self,
        symbol: str,
        day: date,
    ) -> list[FuturesMetricRow]:
        url = self.daily_url(symbol, day)
        async with self._semaphore:
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(45.0, connect=15.0),
                        follow_redirects=True,
                        headers={"User-Agent": "TriConsensus-AI/0.1 research"},
                    ) as client:
                        response = await client.get(url)
                        if response.status_code == 404:
                            return []
                        response.raise_for_status()
                        payload = response.content
                    return parse_metrics_zip(
                        payload,
                        symbol=symbol,
                        archive_day=day,
                    )
                except httpx.HTTPError:
                    if attempt < 2:
                        await asyncio.sleep(0.75 * (2**attempt))
                        continue
                    # Treat a persistently unreachable day as missing data.
                    # historical_metrics still fails closed when coverage <90%.
                    return []
        return []

    async def historical_metrics(
        self,
        symbol: str,
        *,
        days: int,
        end_day: date,
    ) -> list[FuturesMetricRow]:
        self._validate_symbol(symbol)
        if not 7 <= days <= 365:
            raise ValueError("days must be between 7 and 365")

        first_day = end_day - timedelta(days=days - 1)
        requested = [
            first_day + timedelta(days=offset)
            for offset in range(days)
        ]
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=15.0),
            follow_redirects=True,
            headers={"User-Agent": "TriConsensus-AI/0.1 research"},
        ) as client:
            async def fetch_day(day: date) -> list[FuturesMetricRow]:
                url = self.daily_url(symbol, day)
                async with self._semaphore:
                    for attempt in range(3):
                        try:
                            response = await client.get(url)
                            if response.status_code == 404:
                                return []
                            response.raise_for_status()
                            return parse_metrics_zip(
                                response.content,
                                symbol=symbol,
                                archive_day=day,
                            )
                        except httpx.HTTPError:
                            if attempt < 2:
                                await asyncio.sleep(0.75 * (2**attempt))
                                continue
                            return []
                    return []

            results = await asyncio.gather(
                *(fetch_day(day) for day in requested)
            )

        rows_by_ts: dict[int, FuturesMetricRow] = {}
        missing_days = 0
        for rows in results:
            if not rows:
                missing_days += 1
                continue
            for row in rows:
                rows_by_ts[row.timestamp_ms] = row

        if missing_days / days > 0.10:
            raise BinanceVisionFailure(
                f"metrics coverage below 90%: {days - missing_days}/{days} days"
            )

        rows = [rows_by_ts[key] for key in sorted(rows_by_ts)]
        if not rows:
            raise BinanceVisionFailure("no historical metrics rows")
        return rows
