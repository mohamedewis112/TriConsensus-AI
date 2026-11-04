from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
import json
import time
from typing import Deque


OKX_PUBLIC_WS = "wss://ws.okx.com/ws/v5/public"
CHRONOS_WS_NERVOUS_VERSION = "v8.19-ws-nervous-system-0"


class NervousSystemFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class TradeEvent:
    timestamp_ms: int
    price: float
    size_contracts: float
    taker_side: str

    @property
    def signed_size(self) -> float:
        return self.size_contracts if self.taker_side == "buy" else -self.size_contracts


@dataclass(frozen=True)
class NervousFeatureFrame:
    symbol: str
    timestamp_ms: int
    mid_price: float
    spread_bps: float
    microprice_offset_bps: float
    imbalance_l1: float
    imbalance_l5: float
    book_pressure_slope: float
    trade_imbalance_1s: float
    trade_imbalance_5s: float
    trade_intensity_1s: float
    trade_intensity_5s: float
    signed_trade_contracts_1s: float
    signed_trade_contracts_5s: float
    book_updates: int
    trade_updates: int
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def _levels(rows: list[list[str]], depth: int = 5) -> list[tuple[float, float]]:
    result=[]
    for row in rows[:depth]:
        if len(row) < 2:
            continue
        px=float(row[0]); sz=float(row[1])
        if px > 0 and sz >= 0:
            result.append((px,sz))
    return result


def _imbalance(bids, asks, depth: int) -> float:
    bid=sum(sz for _,sz in bids[:depth]); ask=sum(sz for _,sz in asks[:depth])
    total=bid+ask
    return (bid-ask)/total if total > 0 else 0.0


def _book_pressure_slope(bids, asks, mid: float) -> float:
    if mid <= 0:
        return 0.0
    def dist(levels, side):
        total=sum(sz for _,sz in levels)
        if total <= 0:
            return 0.0
        if side=="bid":
            return sum(((mid-px)/mid)*sz for px,sz in levels)/total
        return sum(((px-mid)/mid)*sz for px,sz in levels)/total
    bid=dist(bids,"bid"); ask=dist(asks,"ask")
    return (bid-ask)/max(bid+ask,1e-12)


class TradeTape:
    def __init__(self, max_window_ms: int = 5_000) -> None:
        self.max_window_ms=max_window_ms
        self.events: Deque[TradeEvent]=deque()

    def add(self,event: TradeEvent) -> None:
        self.events.append(event)
        self._trim(event.timestamp_ms)

    def _trim(self,now_ms: int) -> None:
        cutoff=now_ms-self.max_window_ms
        while self.events and self.events[0].timestamp_ms < cutoff:
            self.events.popleft()

    def features(self,now_ms: int,window_ms: int) -> tuple[float,float,float]:
        self._trim(now_ms)
        rows=[e for e in self.events if e.timestamp_ms >= now_ms-window_ms]
        if not rows:
            return 0.0,0.0,0.0
        signed=sum(e.signed_size for e in rows)
        absolute=sum(abs(e.signed_size) for e in rows)
        imbalance=signed/absolute if absolute > 0 else 0.0
        intensity=len(rows)/max(window_ms/1000.0,1e-9)
        return float(imbalance),float(intensity),float(signed)


class NervousAccumulator:
    def __init__(self,symbols: tuple[str,...]=("BTCUSDT","ETHUSDT")) -> None:
        self.symbols=symbols
        self.books={}
        self.tapes={s:TradeTape() for s in symbols}
        self.book_updates={s:0 for s in symbols}
        self.trade_updates={s:0 for s in symbols}

    @staticmethod
    def to_okx(symbol: str) -> str:
        return f"{symbol.removesuffix('USDT')}-USDT-SWAP"

    @staticmethod
    def from_okx(inst_id: str) -> str:
        return inst_id.split("-")[0]+"USDT"

    def handle_message(self,message: dict) -> None:
        if message.get("event")=="error":
            raise NervousSystemFailure(f"OKX websocket error {message.get('code')}: {message.get('msg')}")
        arg=message.get("arg") or {}
        channel=arg.get("channel"); inst_id=arg.get("instId"); data=message.get("data") or []
        if not channel or not inst_id or not data:
            return
        symbol=self.from_okx(str(inst_id))
        if symbol not in self.symbols:
            return
        if channel=="books5":
            row=data[0]
            bids=_levels(row.get("bids") or []); asks=_levels(row.get("asks") or [])
            if bids and asks:
                self.books[symbol]={"timestamp_ms":int(row.get("ts") or time.time()*1000),"bids":bids,"asks":asks}
                self.book_updates[symbol]+=1
        elif channel=="trades":
            for t in data:
                side=str(t.get("side") or "")
                if side not in {"buy","sell"}:
                    continue
                self.tapes[symbol].add(TradeEvent(
                    timestamp_ms=int(t.get("ts") or time.time()*1000),
                    price=float(t["px"]),
                    size_contracts=float(t["sz"]),
                    taker_side=side,
                ))
                self.trade_updates[symbol]+=1

    def frame(self,symbol: str,*,now_ms: int|None=None) -> NervousFeatureFrame:
        book=self.books.get(symbol)
        if not book:
            raise NervousSystemFailure(f"no book snapshot for {symbol}")
        bids=book["bids"]; asks=book["asks"]
        best_bid,bid_sz=bids[0]; best_ask,ask_sz=asks[0]
        mid=(best_bid+best_ask)/2.0
        spread=((best_ask-best_bid)/mid)*10000 if mid>0 else 0.0
        denom=bid_sz+ask_sz
        micro=((best_ask*bid_sz+best_bid*ask_sz)/denom) if denom>0 else mid
        micro_bps=((micro-mid)/mid)*10000 if mid>0 else 0.0
        ts=int(now_ms or book["timestamp_ms"])
        f1,i1,s1=self.tapes[symbol].features(ts,1000)
        f5,i5,s5=self.tapes[symbol].features(ts,5000)
        return NervousFeatureFrame(
            symbol=symbol,timestamp_ms=ts,mid_price=mid,spread_bps=spread,
            microprice_offset_bps=micro_bps,
            imbalance_l1=_imbalance(bids,asks,1),
            imbalance_l5=_imbalance(bids,asks,5),
            book_pressure_slope=_book_pressure_slope(bids,asks,mid),
            trade_imbalance_1s=f1,trade_imbalance_5s=f5,
            trade_intensity_1s=i1,trade_intensity_5s=i5,
            signed_trade_contracts_1s=s1,signed_trade_contracts_5s=s5,
            book_updates=self.book_updates[symbol],trade_updates=self.trade_updates[symbol],
        )


async def collect_public_ws_probe(seconds: float = 8.0) -> dict:
    import websockets

    acc=NervousAccumulator()
    args=[]
    for symbol in acc.symbols:
        inst=acc.to_okx(symbol)
        args.extend([
            {"channel":"books5","instId":inst},
            {"channel":"trades","instId":inst},
        ])
    deadline=time.monotonic()+seconds
    async with websockets.connect(
        OKX_PUBLIC_WS,ping_interval=15,ping_timeout=15,max_size=4*1024*1024,open_timeout=8
    ) as ws:
        await ws.send(json.dumps({"id":"chronos-v819","op":"subscribe","args":args}))
        while time.monotonic()<deadline:
            remaining=max(0.1,deadline-time.monotonic())
            try:
                payload=await asyncio.wait_for(ws.recv(),timeout=min(2.0,remaining))
            except asyncio.TimeoutError:
                continue
            acc.handle_message(json.loads(payload))
    frames={symbol:asdict(acc.frame(symbol)) for symbol in acc.symbols}
    for symbol in acc.symbols:
        if acc.book_updates[symbol] < 2:
            raise NervousSystemFailure(f"insufficient books5 updates for {symbol}")
        if acc.trade_updates[symbol] < 1:
            raise NervousSystemFailure(f"insufficient trades for {symbol}")
    return {
        "version":CHRONOS_WS_NERVOUS_VERSION,
        "source":"OKX_PUBLIC_WS_BOOKS5_TRADES",
        "seconds":seconds,
        "frames":frames,
        "book_updates":dict(acc.book_updates),
        "trade_updates":dict(acc.trade_updates),
        "research_only":True,
        "paper_trading_authority":False,
        "live_trading_authority":False,
    }


async def _main() -> None:
    print(json.dumps(await collect_public_ws_probe(),indent=2,sort_keys=True))


if __name__=="__main__":
    asyncio.run(_main())
