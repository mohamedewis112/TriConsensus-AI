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


def parse_orderbook(result: dict) -> dict:
    bids = [
        {"price": float(price), "size": float(size)}
        for price, size in (result.get("b") or [])
    ]
    asks = [
        {"price": float(price), "size": float(size)}
        for price, size in (result.get("a") or [])
    ]
    if not bids or not asks:
        raise MarketDataFailure("empty orderbook side")

    bid_size = sum(level["size"] for level in bids)
    ask_size = sum(level["size"] for level in asks)
    total = bid_size + ask_size
    imbalance = (bid_size - ask_size) / total if total > 0 else 0.0

    best_bid = bids[0]["price"]
    best_ask = asks[0]["price"]
    mid = (best_bid + best_ask) / 2
    spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else 0.0

    return {
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_depth": bid_size,
        "ask_depth": ask_size,
        "imbalance": imbalance,
        "spread_bps": spread_bps,
        "timestamp_ms": int(result.get("ts") or 0),
        "update_id": result.get("u"),
        "sequence": result.get("seq"),
        "matching_engine_timestamp_ms": result.get("cts"),
    }


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

    async def orderbook(self, symbol: str, *, limit: int = 50) -> dict:
        self._validate_symbol(symbol)
        if not 1 <= limit <= 1000:
            raise ValueError("orderbook limit must be between 1 and 1000")
        body = await self._get(
            "/v5/market/orderbook",
            {"category": "linear", "symbol": symbol, "limit": limit},
        )
        result = body.get("result") or {}
        return parse_orderbook(result)


    async def implied_volatility(self, symbol: str) -> dict:
        """Approximate front-expiry ATM IV from Bybit option mark IV.

        This is an implied-volatility input, not the historical-volatility endpoint.
        We select the nearest non-expired option expiry and liquidity-weight mark IV
        across near-ATM contracts (|delta| in [0.25, 0.75]).
        """
        self._validate_symbol(symbol)
        base_coin = "BTC" if symbol == "BTCUSDT" else "ETH"
        body = await self._get(
            "/v5/market/tickers",
            {"category": "option", "baseCoin": base_coin},
        )
        rows = body.get("result", {}).get("list") or []
        if not rows:
            raise MarketDataFailure(f"empty option ticker response for {base_coin}")

        now = datetime.now(UTC)
        candidates: list[tuple[datetime, float, float, str]] = []
        for item in rows:
            try:
                parts = str(item["symbol"]).split("-")
                if len(parts) < 4:
                    continue
                expiry = datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=UTC)
                if expiry <= now:
                    continue
                mark_iv = float(item.get("markIv") or 0)
                delta = abs(float(item.get("delta") or 0))
                open_interest = float(item.get("openInterest") or 0)
                if mark_iv <= 0 or not 0.25 <= delta <= 0.75 or open_interest <= 0:
                    continue
                candidates.append(
                    (expiry, mark_iv, open_interest, str(item["symbol"]))
                )
            except (KeyError, TypeError, ValueError):
                continue

        if not candidates:
            raise MarketDataFailure(f"no liquid near-ATM option IV for {base_coin}")

        nearest_expiry = min(row[0] for row in candidates)
        front = [row for row in candidates if row[0] == nearest_expiry]
        total_oi = sum(row[2] for row in front)
        if total_oi <= 0:
            raise MarketDataFailure("invalid option open interest")
        mark_iv = sum(row[1] * row[2] for row in front) / total_oi
        return {
            "base_coin": base_coin,
            "mark_iv": mark_iv,
            "expiry": nearest_expiry.isoformat(),
            "sample_count": len(front),
            "open_interest_weight": total_oi,
            "captured_at": datetime.now(UTC).isoformat(),
            "source": "bybit_option_tickers_mark_iv",
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

    async def historical_klines(
        self,
        symbol: str,
        timeframe: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[dict]:
        self._validate_symbol(symbol)
        if timeframe not in INTERVALS:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        if start_ms <= 0 or end_ms <= start_ms:
            raise ValueError("invalid historical time range")

        rows_by_start: dict[int, dict] = {}
        page_end = end_ms

        while page_end >= start_ms:
            body = await self._get(
                "/v5/market/kline",
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

            page = parse_klines(raw_rows, drop_open_candle=False)
            for candle in page:
                candle_start = int(candle["start_ms"])
                if start_ms <= candle_start <= end_ms:
                    rows_by_start[candle_start] = candle

            oldest = min(int(row[0]) for row in raw_rows)
            if oldest <= start_ms or len(raw_rows) < 1000:
                break
            page_end = oldest - 1

        candles = [rows_by_start[key] for key in sorted(rows_by_start)]
        if not candles:
            raise MarketDataFailure(
                f"no historical klines for {symbol} {timeframe} in requested range"
            )
        return candles

    async def multi_timeframe_snapshot(self, symbol: str, *, limit: int = 200) -> dict:
        ticker = await self.ticker(symbol)
        orderbook = await self.orderbook(symbol, limit=50)
        candles = {}
        for timeframe in INTERVALS:
            candles[timeframe] = await self.klines(symbol, timeframe, limit=limit)

        now_ms = int(time.time() * 1000)
        timestamps = [
            int(ticker["server_time_ms"]),
            int(orderbook["timestamp_ms"] or ticker["server_time_ms"]),
        ]
        oldest_component_ms = min(timestamps)
        data_age_ms = max(0, now_ms - oldest_component_ms)
        return {
            "ticker": ticker,
            "orderbook": orderbook,
            "candles": candles,
            "market_data_age_ms": data_age_ms,
        }
