from __future__ import annotations

import asyncio
import calendar
from datetime import UTC, date, datetime
import json

import httpx


MONTHLY_BASE = "https://public.bybit.com/kline_for_metatrader4"
DAILY_BASE = "https://public.bybit.com/trading"
BYBIT_V5 = "https://api.bytick.com"


def monthly_url(symbol: str, year: int, month: int) -> str:
    last = calendar.monthrange(year, month)[1]
    name = f"{symbol}_15_{year:04d}-{month:02d}-01_{year:04d}-{month:02d}-{last:02d}.csv.gz"
    return f"{MONTHLY_BASE}/{symbol}/{year:04d}/{name}"


def daily_url(symbol: str, day: date) -> str:
    return f"{DAILY_BASE}/{symbol}/{symbol}{day.isoformat()}.csv.gz"


async def probe_url(client: httpx.AsyncClient, url: str) -> dict:
    try:
        async with client.stream("GET", url) as response:
            prefix = b""
            if response.status_code == 200:
                async for chunk in response.aiter_bytes():
                    prefix += chunk
                    if len(prefix) >= 64:
                        break
            return {
                "status": response.status_code,
                "content_length": response.headers.get("content-length"),
                "content_type": response.headers.get("content-type"),
                "prefix_hex": prefix[:16].hex(),
                "url": url,
            }
    except Exception as exc:
        return {
            "status": None,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "url": url,
        }



async def probe_v5_15m(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    start_day: date,
    end_day: date,
) -> dict:
    start_ms = int(datetime.combine(start_day, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.combine(end_day, datetime.max.time(), tzinfo=UTC).timestamp() * 1000)
    try:
        response = await client.get(
            f"{BYBIT_V5}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": "15",
                "start": start_ms,
                "end": end_ms,
                "limit": 1000,
            },
        )
        response.raise_for_status()
        body = response.json()
        rows = (body.get("result") or {}).get("list") or []
        starts = sorted(int(row[0]) for row in rows if row)
        return {
            "http_status": response.status_code,
            "retCode": body.get("retCode"),
            "retMsg": body.get("retMsg"),
            "rows": len(rows),
            "oldest_start_ms": starts[0] if starts else None,
            "newest_start_ms": starts[-1] if starts else None,
            "url": str(response.url),
        }
    except Exception as exc:
        return {
            "http_status": None,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


async def main() -> None:
    timeout = httpx.Timeout(30.0, connect=10.0)
    headers = {"User-Agent": "TriConsensus-AI/Q1-archive-probe"}
    report: dict[str, object] = {"monthly": {}, "daily_samples": {}, "v5_15m": {}}
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            monthly = {}
            for year, month in ((2025, 10), (2025, 11), (2025, 12), (2026, 1)):
                key = f"{year:04d}-{month:02d}"
                monthly[key] = await probe_url(client, monthly_url(symbol, year, month))
            report["monthly"][symbol] = monthly

            daily = {}
            for day in (
                date(2025, 10, 21),
                date(2025, 10, 31),
                date(2025, 11, 1),
                date(2025, 12, 31),
                date(2026, 1, 1),
                date(2026, 1, 19),
            ):
                daily[day.isoformat()] = await probe_url(client, daily_url(symbol, day))
            report["daily_samples"][symbol] = daily

            report["v5_15m"][symbol] = {
                "2025-12": await probe_v5_15m(
                    client,
                    symbol,
                    start_day=date(2025, 12, 1),
                    end_day=date(2025, 12, 31),
                ),
                "2026-01": await probe_v5_15m(
                    client,
                    symbol,
                    start_day=date(2026, 1, 1),
                    end_day=date(2026, 1, 19),
                ),
            }

    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
