from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
ALLOWED_INTERVALS = {"5min", "15min", "30min", "1h", "4h", "1d"}


class DerivativesDataFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class FundingPoint:
    symbol: str
    timestamp_ms: int
    funding_rate: float


@dataclass(frozen=True)
class OpenInterestPoint:
    symbol: str
    timestamp_ms: int
    open_interest: float


@dataclass(frozen=True)
class AccountRatioPoint:
    symbol: str
    timestamp_ms: int
    buy_ratio: float
    sell_ratio: float
    long_short_ratio: float


class BybitDerivativesClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if symbol not in ALLOWED_SYMBOLS:
            raise ValueError("V4.2 supports BTCUSDT and ETHUSDT only")

    @staticmethod
    def _validate_range(start_ms: int, end_ms: int) -> None:
        if start_ms <= 0 or end_ms <= start_ms:
            raise ValueError("invalid derivatives history range")

    async def _get(self, path: str, params: dict[str, Any]) -> dict:
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"User-Agent": "TriConsensus-AI/0.2 research"},
        )
        try:
            response = await client.get(
                f"{self.settings.bybit_base_url.rstrip('/')}{path}",
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DerivativesDataFailure(type(exc).__name__) from exc
        finally:
            if own_client:
                await client.aclose()

        if int(payload.get("retCode", -1)) != 0:
            raise DerivativesDataFailure(
                f"Bybit retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}"
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise DerivativesDataFailure("missing Bybit result object")
        return result

    async def funding_history(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[FundingPoint]:
        self._validate_symbol(symbol)
        self._validate_range(start_ms, end_ms)

        points: dict[int, FundingPoint] = {}
        page_end = end_ms
        previous_oldest: int | None = None

        for _ in range(100):
            result = await self._get(
                "/v5/market/funding/history",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "endTime": page_end,
                    "limit": 200,
                },
            )
            rows = result.get("list") or []
            if not rows:
                break

            timestamps: list[int] = []
            for row in rows:
                try:
                    ts = int(row["fundingRateTimestamp"])
                    rate = float(row["fundingRate"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise DerivativesDataFailure("invalid funding row") from exc
                timestamps.append(ts)
                if start_ms <= ts <= end_ms:
                    points[ts] = FundingPoint(symbol, ts, rate)

            oldest = min(timestamps)
            if oldest <= start_ms:
                break
            if previous_oldest is not None and oldest >= previous_oldest:
                raise DerivativesDataFailure("funding pagination did not advance")
            previous_oldest = oldest
            page_end = oldest - 1
        else:
            raise DerivativesDataFailure("funding pagination safety limit exceeded")

        return [points[key] for key in sorted(points)]

    async def open_interest_history(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
        interval: str = "15min",
    ) -> list[OpenInterestPoint]:
        self._validate_symbol(symbol)
        self._validate_range(start_ms, end_ms)
        if interval not in ALLOWED_INTERVALS:
            raise ValueError("unsupported open-interest interval")

        points: dict[int, OpenInterestPoint] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for _ in range(500):
            params: dict[str, Any] = {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor

            result = await self._get("/v5/market/open-interest", params)
            for row in result.get("list") or []:
                try:
                    ts = int(row["timestamp"])
                    value = float(row["openInterest"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise DerivativesDataFailure("invalid open-interest row") from exc
                if value < 0:
                    raise DerivativesDataFailure("negative open interest")
                if start_ms <= ts <= end_ms:
                    points[ts] = OpenInterestPoint(symbol, ts, value)

            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise DerivativesDataFailure("open-interest cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise DerivativesDataFailure("open-interest pagination safety limit exceeded")

        return [points[key] for key in sorted(points)]

    async def account_ratio_history(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
        period: str = "15min",
    ) -> list[AccountRatioPoint]:
        self._validate_symbol(symbol)
        self._validate_range(start_ms, end_ms)
        if period not in ALLOWED_INTERVALS:
            raise ValueError("unsupported account-ratio period")

        points: dict[int, AccountRatioPoint] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for _ in range(500):
            params: dict[str, Any] = {
                "category": "linear",
                "symbol": symbol,
                "period": period,
                "startTime": str(start_ms),
                "endTime": str(end_ms),
                "limit": 500,
            }
            if cursor:
                params["cursor"] = cursor

            result = await self._get("/v5/market/account-ratio", params)
            for row in result.get("list") or []:
                try:
                    ts = int(row["timestamp"])
                    buy = float(row["buyRatio"])
                    sell = float(row["sellRatio"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise DerivativesDataFailure("invalid account-ratio row") from exc
                if buy < 0 or sell < 0 or (buy + sell) <= 0:
                    raise DerivativesDataFailure("invalid account ratios")
                if start_ms <= ts <= end_ms:
                    points[ts] = AccountRatioPoint(
                        symbol=symbol,
                        timestamp_ms=ts,
                        buy_ratio=buy,
                        sell_ratio=sell,
                        long_short_ratio=buy / sell if sell > 0 else float("inf"),
                    )

            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise DerivativesDataFailure("account-ratio cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise DerivativesDataFailure("account-ratio pagination safety limit exceeded")

        return [points[key] for key in sorted(points)]


def align_derivatives_to_15m(
    *,
    timestamps_ms: list[int],
    funding: list[FundingPoint],
    open_interest: list[OpenInterestPoint],
    account_ratio: list[AccountRatioPoint],
) -> list[dict]:
    if timestamps_ms != sorted(timestamps_ms):
        raise ValueError("timestamps must be sorted")

    funding_map = {row.timestamp_ms: row for row in funding}
    oi_map = {row.timestamp_ms: row for row in open_interest}
    ratio_map = {row.timestamp_ms: row for row in account_ratio}

    funding_keys = sorted(funding_map)
    rows: list[dict] = []
    last_funding: FundingPoint | None = None
    funding_index = 0

    for ts in timestamps_ms:
        while (
            funding_index < len(funding_keys)
            and funding_keys[funding_index] <= ts
        ):
            last_funding = funding_map[funding_keys[funding_index]]
            funding_index += 1

        oi = oi_map.get(ts)
        ratio = ratio_map.get(ts)
        rows.append(
            {
                "timestamp_ms": ts,
                "funding_rate": (
                    last_funding.funding_rate if last_funding is not None else None
                ),
                "open_interest": oi.open_interest if oi is not None else None,
                "buy_ratio": ratio.buy_ratio if ratio is not None else None,
                "sell_ratio": ratio.sell_ratio if ratio is not None else None,
                "long_short_ratio": (
                    ratio.long_short_ratio if ratio is not None else None
                ),
            }
        )
    return rows
