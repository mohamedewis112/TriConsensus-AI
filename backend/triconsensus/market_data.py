from __future__ import annotations

from datetime import UTC, datetime
import httpx

from .config import Settings


class MarketDataFailure(RuntimeError):
    pass


class BybitPublicClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.bybit_base_url.rstrip("/")

    async def ticker(self, symbol: str) -> dict:
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("V1 supports BTCUSDT and ETHUSDT only")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{self.base_url}/v5/market/tickers",
                    params={"category": "linear", "symbol": symbol},
                )
                r.raise_for_status()
                body = r.json()
        except httpx.HTTPError as exc:
            raise MarketDataFailure(str(exc)) from exc

        if body.get("retCode") != 0 or not body.get("result", {}).get("list"):
            raise MarketDataFailure(f"Bybit error: {body.get('retMsg', 'unknown')}")

        item = body["result"]["list"][0]
        return {
            "symbol": symbol,
            "last_price": float(item["lastPrice"]),
            "bid": float(item["bid1Price"]),
            "ask": float(item["ask1Price"]),
            "funding_rate": float(item.get("fundingRate") or 0),
            "open_interest": float(item.get("openInterest") or 0),
            "captured_at": datetime.now(UTC).isoformat(),
        }
