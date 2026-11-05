from __future__ import annotations

from dataclasses import dataclass
import asyncio
import math

import httpx


class DerivativesContextFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class DerivativesPoint:
    timestamp_ms: int
    value: float


@dataclass(frozen=True)
class AccountRatioPoint:
    timestamp_ms: int
    buy_ratio: float
    sell_ratio: float


class BybitDerivativesContextClient:
    def __init__(
        self,
        base_url: str = "https://api.bybit.com",
        *,
        concurrency: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._semaphore = asyncio.Semaphore(concurrency)

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("supported symbols are BTCUSDT and ETHUSDT")

    async def _get(self, path: str, params: dict) -> dict:
        async with self._semaphore:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    follow_redirects=True,
                    headers={"User-Agent": "TriConsensus-AI/0.1 research"},
                ) as client:
                    response = await client.get(
                        f"{self.base_url}{path}",
                        params=params,
                    )
                    response.raise_for_status()
                    body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise DerivativesContextFailure(
                    f"{path} failed: {type(exc).__name__}"
                ) from exc

        if body.get("retCode") != 0:
            raise DerivativesContextFailure(
                f"{path} Bybit error: {body.get('retMsg', 'unknown')}"
            )
        return body

    async def open_interest_15m(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[DerivativesPoint]:
        self._validate_symbol(symbol)
        cursor: str | None = None
        points: dict[int, DerivativesPoint] = {}

        while True:
            params = {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": "15min",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            body = await self._get("/v5/market/open-interest", params)
            result = body.get("result") or {}
            for row in result.get("list") or []:
                timestamp = int(row["timestamp"])
                value = float(row["openInterest"])
                if start_ms <= timestamp <= end_ms and math.isfinite(value):
                    points[timestamp] = DerivativesPoint(timestamp, value)
            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        return [points[key] for key in sorted(points)]

    async def account_ratio_15m(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[AccountRatioPoint]:
        self._validate_symbol(symbol)
        cursor: str | None = None
        points: dict[int, AccountRatioPoint] = {}

        while True:
            params = {
                "category": "linear",
                "symbol": symbol,
                "period": "15min",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 500,
            }
            if cursor:
                params["cursor"] = cursor
            body = await self._get("/v5/market/account-ratio", params)
            result = body.get("result") or {}
            for row in result.get("list") or []:
                timestamp = int(row["timestamp"])
                buy = float(row["buyRatio"])
                sell = float(row["sellRatio"])
                if (
                    start_ms <= timestamp <= end_ms
                    and math.isfinite(buy)
                    and math.isfinite(sell)
                    and buy >= 0
                    and sell >= 0
                ):
                    points[timestamp] = AccountRatioPoint(
                        timestamp,
                        buy,
                        sell,
                    )
            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        return [points[key] for key in sorted(points)]

    async def funding_history(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[DerivativesPoint]:
        self._validate_symbol(symbol)
        points: dict[int, DerivativesPoint] = {}
        page_end = end_ms

        while page_end >= start_ms:
            body = await self._get(
                "/v5/market/funding/history",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "startTime": start_ms,
                    "endTime": page_end,
                    "limit": 200,
                },
            )
            rows = (body.get("result") or {}).get("list") or []
            if not rows:
                break

            timestamps = []
            for row in rows:
                timestamp = int(row["fundingRateTimestamp"])
                timestamps.append(timestamp)
                value = float(row["fundingRate"])
                if start_ms <= timestamp <= end_ms and math.isfinite(value):
                    points[timestamp] = DerivativesPoint(timestamp, value)

            oldest = min(timestamps)
            if oldest <= start_ms or len(rows) < 200:
                break
            page_end = oldest - 1

        return [points[key] for key in sorted(points)]

    async def premium_index_15m(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[DerivativesPoint]:
        self._validate_symbol(symbol)
        points: dict[int, DerivativesPoint] = {}
        page_end = end_ms

        while page_end >= start_ms:
            body = await self._get(
                "/v5/market/premium-index-price-kline",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "interval": "15",
                    "start": start_ms,
                    "end": page_end,
                    "limit": 1000,
                },
            )
            rows = (body.get("result") or {}).get("list") or []
            if not rows:
                break

            timestamps = []
            for row in rows:
                timestamp = int(row[0])
                timestamps.append(timestamp)
                value = float(row[4])
                if start_ms <= timestamp <= end_ms and math.isfinite(value):
                    points[timestamp] = DerivativesPoint(timestamp, value)

            oldest = min(timestamps)
            if oldest <= start_ms or len(rows) < 1000:
                break
            page_end = oldest - 1

        return [points[key] for key in sorted(points)]

    async def snapshot_history(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> dict:
        if end_ms <= start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        oi, ratio, funding, premium = await asyncio.gather(
            self.open_interest_15m(
                symbol,
                start_ms=start_ms,
                end_ms=end_ms,
            ),
            self.account_ratio_15m(
                symbol,
                start_ms=start_ms,
                end_ms=end_ms,
            ),
            self.funding_history(
                symbol,
                start_ms=start_ms,
                end_ms=end_ms,
            ),
            self.premium_index_15m(
                symbol,
                start_ms=start_ms,
                end_ms=end_ms,
            ),
        )
        return {
            "open_interest": oi,
            "account_ratio": ratio,
            "funding": funding,
            "premium_index": premium,
        }
