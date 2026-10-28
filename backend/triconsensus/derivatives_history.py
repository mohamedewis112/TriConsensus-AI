from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

from .market_data import BybitPublicClient, INTERVALS, MarketDataFailure


@dataclass(frozen=True)
class DerivativesHistory:
    open_interest: list[dict]
    funding: list[dict]
    mark_klines: list[dict]
    index_klines: list[dict]
    premium_klines: list[dict]


def _parse_price_rows(rows: list[list[str]]) -> list[dict]:
    parsed = [
        {
            "start_ms": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        }
        for row in rows
        if len(row) >= 5
    ]
    parsed.sort(key=lambda row: row["start_ms"])
    return parsed


class BybitDerivativesHistoryClient:
    def __init__(self, public_client: BybitPublicClient) -> None:
        self.client = public_client

    async def historical_open_interest(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
        interval: str = "15min",
    ) -> list[dict]:
        self.client._validate_symbol(symbol)
        if interval not in {"5min", "15min", "30min", "1h", "4h", "1d"}:
            raise ValueError("unsupported open-interest interval")
        if start_ms <= 0 or end_ms <= start_ms:
            raise ValueError("invalid open-interest range")

        rows_by_ts: dict[int, dict] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()

        while True:
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

            body = await self.client._get("/v5/market/open-interest", params)
            result = body.get("result") or {}
            raw_rows = result.get("list") or []
            for row in raw_rows:
                timestamp = int(row["timestamp"])
                if start_ms <= timestamp <= end_ms:
                    rows_by_ts[timestamp] = {
                        "timestamp_ms": timestamp,
                        "open_interest": float(row["openInterest"]),
                        "single_open_interest": (
                            float(row["singleOpenInterest"])
                            if row.get("singleOpenInterest") not in (None, "")
                            else None
                        ),
                    }

            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor or not raw_rows:
                break
            if next_cursor in seen_cursors:
                raise MarketDataFailure("open-interest pagination cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        rows = [rows_by_ts[key] for key in sorted(rows_by_ts)]
        if not rows:
            raise MarketDataFailure("empty historical open-interest response")
        return rows

    async def historical_funding(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[dict]:
        self.client._validate_symbol(symbol)
        if start_ms <= 0 or end_ms <= start_ms:
            raise ValueError("invalid funding range")

        rows_by_ts: dict[int, dict] = {}
        page_end = end_ms

        while page_end >= start_ms:
            body = await self.client._get(
                "/v5/market/funding/history",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "startTime": start_ms,
                    "endTime": page_end,
                    "limit": 200,
                },
            )
            raw_rows = body.get("result", {}).get("list") or []
            if not raw_rows:
                break

            oldest = page_end
            for row in raw_rows:
                timestamp = int(row["fundingRateTimestamp"])
                oldest = min(oldest, timestamp)
                if start_ms <= timestamp <= end_ms:
                    rows_by_ts[timestamp] = {
                        "timestamp_ms": timestamp,
                        "funding_rate": float(row["fundingRate"]),
                    }

            if oldest <= start_ms or len(raw_rows) < 200:
                break
            if oldest >= page_end:
                raise MarketDataFailure("funding pagination did not move backwards")
            page_end = oldest - 1

        rows = [rows_by_ts[key] for key in sorted(rows_by_ts)]
        if not rows:
            raise MarketDataFailure("empty historical funding response")
        return rows

    async def historical_reference_klines(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
        kind: str,
        timeframe: str = "15m",
    ) -> list[dict]:
        self.client._validate_symbol(symbol)
        if timeframe not in INTERVALS:
            raise ValueError("unsupported reference-kline timeframe")
        if start_ms <= 0 or end_ms <= start_ms:
            raise ValueError("invalid reference-kline range")

        paths = {
            "mark": "/v5/market/mark-price-kline",
            "index": "/v5/market/index-price-kline",
            "premium": "/v5/market/premium-index-price-kline",
        }
        if kind not in paths:
            raise ValueError("kind must be mark, index, or premium")

        rows_by_start: dict[int, dict] = {}
        page_end = end_ms
        while page_end >= start_ms:
            body = await self.client._get(
                paths[kind],
                {
                    "category": "linear",
                    "symbol": symbol,
                    "interval": INTERVALS[timeframe],
                    "start": start_ms,
                    "end": page_end,
                    "limit": 1000,
                },
            )
            raw_rows = body.get("result", {}).get("list") or []
            if not raw_rows:
                break

            page = _parse_price_rows(raw_rows)
            for candle in page:
                timestamp = int(candle["start_ms"])
                if start_ms <= timestamp <= end_ms:
                    rows_by_start[timestamp] = candle

            oldest = min(int(row[0]) for row in raw_rows)
            if oldest <= start_ms or len(raw_rows) < 1000:
                break
            if oldest >= page_end:
                raise MarketDataFailure(
                    f"{kind} kline pagination did not move backwards"
                )
            page_end = oldest - 1

        rows = [rows_by_start[key] for key in sorted(rows_by_start)]
        if not rows:
            raise MarketDataFailure(f"empty historical {kind} kline response")
        return rows

    async def snapshot_history(
        self,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> DerivativesHistory:
        open_interest = await self.historical_open_interest(
            symbol,
            start_ms=start_ms,
            end_ms=end_ms,
            interval="15min",
        )
        funding = await self.historical_funding(
            symbol,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        mark = await self.historical_reference_klines(
            symbol,
            start_ms=start_ms,
            end_ms=end_ms,
            kind="mark",
        )
        index = await self.historical_reference_klines(
            symbol,
            start_ms=start_ms,
            end_ms=end_ms,
            kind="index",
        )
        premium = await self.historical_reference_klines(
            symbol,
            start_ms=start_ms,
            end_ms=end_ms,
            kind="premium",
        )
        return DerivativesHistory(
            open_interest=open_interest,
            funding=funding,
            mark_klines=mark,
            index_klines=index,
            premium_klines=premium,
        )


def build_derivatives_feature_frame(
    trade_candles: list[dict],
    history: DerivativesHistory,
) -> pd.DataFrame:
    if not trade_candles:
        raise ValueError("trade candles are required")

    frame = pd.DataFrame(trade_candles).copy()
    frame["start_ms"] = pd.to_numeric(frame["start_ms"], errors="raise").astype("int64")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce").astype(float)
    frame = frame.sort_values("start_ms").drop_duplicates("start_ms").reset_index(drop=True)

    def price_map(rows: list[dict]) -> dict[int, float]:
        return {
            int(row["start_ms"]): float(row["close"])
            for row in rows
            if math.isfinite(float(row["close"]))
        }

    mark = price_map(history.mark_klines)
    index = price_map(history.index_klines)
    premium = price_map(history.premium_klines)
    oi = {
        int(row["timestamp_ms"]): float(row["open_interest"])
        for row in history.open_interest
    }

    frame["mark_close"] = frame["start_ms"].map(mark)
    frame["index_close"] = frame["start_ms"].map(index)
    frame["premium_index_close"] = frame["start_ms"].map(premium)
    frame["open_interest"] = frame["start_ms"].map(oi)

    funding = pd.DataFrame(history.funding)
    if funding.empty:
        frame["funding_rate"] = float("nan")
    else:
        funding = funding.sort_values("timestamp_ms")
        frame = pd.merge_asof(
            frame.sort_values("start_ms"),
            funding[["timestamp_ms", "funding_rate"]].rename(
                columns={"timestamp_ms": "funding_timestamp_ms"}
            ),
            left_on="start_ms",
            right_on="funding_timestamp_ms",
            direction="backward",
        )
        frame["funding_rate"] = pd.to_numeric(
            frame["funding_rate"],
            errors="coerce",
        ).astype(float)

    frame["mark_index_basis_bps"] = (
        (frame["mark_close"] / frame["index_close"]) - 1.0
    ) * 10_000.0
    frame["last_index_basis_bps"] = (
        (frame["close"] / frame["index_close"]) - 1.0
    ) * 10_000.0
    frame["open_interest_return_1"] = frame["open_interest"].pct_change()
    frame["open_interest_return_4"] = frame["open_interest"].pct_change(4)
    frame["funding_rate_change"] = frame["funding_rate"].diff()
    frame["basis_change_bps"] = frame["mark_index_basis_bps"].diff()

    return frame
