from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .cycle import PaperDecisionCycle
from .db import PersistenceFailure
from .market_data import BybitPublicClient, MarketDataFailure

app = FastAPI(title="TriConsensus AI API", version="0.1.0")
market = BybitPublicClient(settings)


class AccountState(BaseModel):
    equity: float = Field(gt=0)
    peak_equity: float = Field(gt=0)
    daily_pnl: float = 0
    open_positions: int = Field(default=0, ge=0)
    slippage_bps: float = Field(default=2, ge=0)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "trading_mode": settings.trading_mode,
        "live_trading": False,
    }


@app.get("/v1/market/{symbol}")
async def get_market(symbol: str) -> dict:
    normalized = symbol.upper().replace("-", "").replace("/", "")
    try:
        return await market.multi_timeframe_snapshot(normalized)
    except (ValueError, MarketDataFailure) as exc:
        raise HTTPException(status_code=503, detail=f"NO_TRADE: {exc}") from exc


@app.post("/v1/paper-cycle/{symbol}")
async def paper_cycle(symbol: str, account: AccountState) -> dict:
    try:
        cycle = PaperDecisionCycle(settings)
        return await cycle.run(symbol, account.model_dump())
    except PersistenceFailure as exc:
        raise HTTPException(status_code=503, detail=f"NO_TRADE: {exc}") from exc
