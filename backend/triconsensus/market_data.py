from __future__ import annotations

from datetime import UTC, datetime
import time

import httpx


class MarketDataFailure(RuntimeError):
    pass


INTERVALS = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
}


def parse_klines(rows: list[list[str]], *, drop_open_candle: bool = True) -> list[dict]:
    """Normalize Bybit reverse-chronological kline rows to oldest -> newest."""
    parsed = [
        {
            "start_ms": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "turnover": float(row[6]),
        }
        for row in rows
    ]
    parsed.sort(key=lambda x: x["start_ms"])
    if drop_open_candle and parsed:
        parsed = parsed[:-1]
    return parsed


class BybitPublicClient:
    def __init__(self, settings) -> None:
        self.base_url = settings.bybit_base_url.rstrip("/")

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("V1 supports BTCUSDT and ETHUSDT only")

    async def _get(self, path: str, params: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{self.base_url}{path}", params=params)
                r.raise_for_status()
                body = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MarketDataFailure(str(exc)) from exc

        if body.get("retCode") != 0:
            raise MarketDataFailure(f"Bybit error: {body.get('retMsg', 'unknown')}")
        return body

    async def ticker(self, symbol: str) -> dict:
        self._validate_symbol(symbol)
        request_started_ms = int(time.time() * 1000)
        body = await self._get(
            "/v5/market/tickers",
            {"category": "linear", "symbol": symbol},
        )
        rows = body.get("result", {}).get("list") or []
        if not rows:
            raise MarketDataFailure("empty ticker response")

        item = rows[0]
        received_ms = int(time.time() * 1000)
        return {
            "symbol": symbol,
            "last_price": float(item["lastPrice"]),
            "bid": float(item["bid1Price"]),
            "ask": float(item["ask1Price"]),
            "funding_rate": float(item.get("fundingRate") or 0),
            "open_interest": float(item.get("openInterest") or 0),
            "server_time_ms": int(body.get("time") or received_ms),
            "request_started_ms": request_started_ms,
            "received_ms": received_ms,
            "captured_at": datetime.now(UTC).isoformat(),
        }

    async def klines(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 200,
        drop_open_candle: bool = True,
    ) -> list[dict]:
        self._validate_symbol(symbol)
        if timeframe not in INTERVALS:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        if not 60 <= limit <= 1000:
            raise ValueError("limit must be between 60 and 1000")

        body = await self._get(
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": INTERVALS[timeframe],
                "limit": limit,
            },
        )
        rows = body.get("result", {}).get("list") or []
        if not rows:
            raise MarketDataFailure(f"empty kline response for {symbol} {timeframe}")
        return parse_klines(rows, drop_open_candle=drop_open_candle)

    async def multi_timeframe_snapshot(self, symbol: str, *, limit: int = 200) -> dict:
        ticker = await self.ticker(symbol)
        candles = {}
        for timeframe in INTERVALS:
            candles[timeframe] = await self.klines(symbol, timeframe, limit=limit)

        now_ms = int(time.time() * 1000)
        data_age_ms = max(0, now_ms - int(ticker["server_time_ms"]))
        return {
            "ticker": ticker,
            "candles": candles,
            "market_data_age_ms": data_age_ms,
        }
