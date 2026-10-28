from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
from typing import Awaitable, Callable

try:
    import websockets
except Exception:  # pragma: no cover - optional streaming dependency
    websockets = None


class MicrostructureFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class MicrostructureSnapshot:
    venue: str
    symbol: str
    exchange_timestamp_ms: int
    received_timestamp_ms: int
    bid_depth: float
    ask_depth: float
    imbalance: float
    spread_bps: float
    sequence: int | None = None

    @property
    def age_ms(self) -> int:
        return max(0, int(time.time() * 1000) - self.received_timestamp_ms)


def depth_metrics(bids: list[list[str]], asks: list[list[str]]) -> tuple[float, float, float, float]:
    if not bids or not asks:
        raise MicrostructureFailure("empty orderbook side")
    bid_depth = sum(float(row[1]) for row in bids)
    ask_depth = sum(float(row[1]) for row in asks)
    total = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid = (best_bid + best_ask) / 2.0
    spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else 0.0
    return bid_depth, ask_depth, imbalance, spread_bps


class BybitOrderbookStream:
    """20ms L50 linear orderbook stream with exchange millisecond timestamps."""

    def __init__(self, base_url: str = "wss://stream.bybit.com/v5/public/linear") -> None:
        self.base_url = base_url

    async def run(
        self,
        symbol: str,
        callback: Callable[[MicrostructureSnapshot], Awaitable[None]],
    ) -> None:
        if websockets is None:
            raise MicrostructureFailure("install backend/requirements-marl.txt")
        async with websockets.connect(self.base_url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(
                json.dumps({"op": "subscribe", "args": [f"orderbook.50.{symbol}"]})
            )
            bids: dict[str, str] = {}
            asks: dict[str, str] = {}

            async for raw in ws:
                message = json.loads(raw)
                if message.get("topic") != f"orderbook.50.{symbol}":
                    continue
                data = message.get("data") or {}
                if message.get("type") == "snapshot":
                    bids = {price: size for price, size in data.get("b", [])}
                    asks = {price: size for price, size in data.get("a", [])}
                else:
                    for price, size in data.get("b", []):
                        if float(size) == 0:
                            bids.pop(price, None)
                        else:
                            bids[price] = size
                    for price, size in data.get("a", []):
                        if float(size) == 0:
                            asks.pop(price, None)
                        else:
                            asks[price] = size

                bid_rows = sorted(bids.items(), key=lambda row: float(row[0]), reverse=True)[:50]
                ask_rows = sorted(asks.items(), key=lambda row: float(row[0]))[:50]
                if not bid_rows or not ask_rows:
                    continue
                bid_depth, ask_depth, imbalance, spread_bps = depth_metrics(
                    [[p, q] for p, q in bid_rows],
                    [[p, q] for p, q in ask_rows],
                )
                await callback(
                    MicrostructureSnapshot(
                        venue="bybit",
                        symbol=symbol,
                        exchange_timestamp_ms=int(data.get("cts") or message.get("ts") or 0),
                        received_timestamp_ms=int(time.time() * 1000),
                        bid_depth=bid_depth,
                        ask_depth=ask_depth,
                        imbalance=imbalance,
                        spread_bps=spread_bps,
                        sequence=int(data["seq"]) if data.get("seq") is not None else None,
                    )
                )


class BinanceOrderbookStream:
    """USD-M public partial depth stream using Binance's post-2026 public WS base."""

    def __init__(self, base_url: str = "wss://fstream.binance.com/public/ws") -> None:
        self.base_url = base_url.rstrip("/")

    async def run(
        self,
        symbol: str,
        callback: Callable[[MicrostructureSnapshot], Awaitable[None]],
    ) -> None:
        if websockets is None:
            raise MicrostructureFailure("install backend/requirements-marl.txt")
        stream = f"{symbol.lower()}@depth20@100ms"
        async with websockets.connect(
            f"{self.base_url}/{stream}",
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            async for raw in ws:
                data = json.loads(raw)
                bids = data.get("b") or data.get("bids") or []
                asks = data.get("a") or data.get("asks") or []
                if not bids or not asks:
                    continue
                bid_depth, ask_depth, imbalance, spread_bps = depth_metrics(bids, asks)
                await callback(
                    MicrostructureSnapshot(
                        venue="binance",
                        symbol=symbol,
                        exchange_timestamp_ms=int(data.get("E") or time.time() * 1000),
                        received_timestamp_ms=int(time.time() * 1000),
                        bid_depth=bid_depth,
                        ask_depth=ask_depth,
                        imbalance=imbalance,
                        spread_bps=spread_bps,
                        sequence=int(data["u"]) if data.get("u") is not None else None,
                    )
                )


async def run_cross_venue_collectors(
    symbol: str,
    callback: Callable[[MicrostructureSnapshot], Awaitable[None]],
) -> None:
    await asyncio.gather(
        BybitOrderbookStream().run(symbol, callback),
        BinanceOrderbookStream().run(symbol, callback),
    )
