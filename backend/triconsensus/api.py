from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException

from .config import settings
from .cycle import PaperDecisionCycle
from .db import PersistenceFailure, SupabaseRepository
from .market_data import BybitPublicClient, MarketDataFailure
from .metrics import calculate_metrics
from .paper_monitor import PaperTradeMonitor
from .preflight import run_preflight
from .security import require_control_key

app = FastAPI(title="TriConsensus AI API", version="0.1.0")
market = BybitPublicClient(settings)


def _empty_summary(version: str) -> dict:
    metrics = calculate_metrics([], starting_equity=settings.paper_starting_balance)
    return {
        "version": version,
        "paper_balance": settings.paper_starting_balance,
        "equity": settings.paper_starting_balance,
        "today_pnl": 0.0,
        "open_positions": 0,
        "closed_trades": 0,
        "kill_switch_active": settings.emergency_kill_switch,
        **asdict(metrics),
    }


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "trading_mode": settings.trading_mode,
        "live_trading": False,
        "kill_switch_active": settings.emergency_kill_switch,
    }


@app.get("/v1/preflight", dependencies=[Depends(require_control_key)])
async def preflight() -> dict:
    return await run_preflight(settings)


@app.get("/v1/market/{symbol}", dependencies=[Depends(require_control_key)])
async def get_market(symbol: str) -> dict:
    normalized = symbol.upper().replace("-", "").replace("/", "")
    try:
        return await market.multi_timeframe_snapshot(normalized)
    except (ValueError, MarketDataFailure) as exc:
        raise HTTPException(status_code=503, detail=f"NO_TRADE: {exc}") from exc


@app.post("/v1/paper-cycle/{symbol}", dependencies=[Depends(require_control_key)])
async def paper_cycle(symbol: str) -> dict:
    try:
        return await PaperDecisionCycle(settings).run(symbol)
    except PersistenceFailure as exc:
        raise HTTPException(status_code=503, detail=f"NO_TRADE: {exc}") from exc


@app.post("/v1/paper-monitor", dependencies=[Depends(require_control_key)])
async def paper_monitor(version: str = "V1") -> dict:
    try:
        return await PaperTradeMonitor(settings).run(version=version)
    except PersistenceFailure as exc:
        raise HTTPException(status_code=503, detail=f"NO_ACTION: {exc}") from exc


@app.get("/v1/analytics/summary", dependencies=[Depends(require_control_key)])
async def analytics_summary(version: str = "V1") -> dict:
    try:
        repo = SupabaseRepository(settings)
        experiment = repo.get_experiment(version)
        if experiment is None:
            return _empty_summary(version)

        trades = repo.list_trades(experiment["id"], limit=1000)
        closed = [t for t in trades if t.get("status") == "closed"]
        open_trades = [t for t in trades if t.get("status") == "open"]
        pnls = [float(t.get("pnl_net") or 0) for t in reversed(closed)]
        metrics = calculate_metrics(
            pnls,
            starting_equity=settings.paper_starting_balance,
        )
        today = datetime.now(UTC).date().isoformat()
        today_pnl = sum(
            float(t.get("pnl_net") or 0)
            for t in closed
            if str(t.get("closed_at") or "").startswith(today)
        )
        paper_balance = settings.paper_starting_balance + metrics.net_pnl
        return {
            "version": version,
            "experiment_id": experiment["id"],
            "experiment_status": experiment["status"],
            "paper_balance": paper_balance,
            "equity": paper_balance,
            "today_pnl": today_pnl,
            "open_positions": len(open_trades),
            "closed_trades": len(closed),
            "kill_switch_active": settings.emergency_kill_switch,
            **asdict(metrics),
        }
    except PersistenceFailure as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/v1/trades", dependencies=[Depends(require_control_key)])
async def trades(version: str = "V1", limit: int = 100) -> dict:
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    try:
        repo = SupabaseRepository(settings)
        experiment = repo.get_experiment(version)
        if experiment is None:
            return {"version": version, "trades": []}
        return {
            "version": version,
            "trades": repo.list_trades(experiment["id"], limit=limit),
        }
    except PersistenceFailure as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/v1/replay/{consensus_id}", dependencies=[Depends(require_control_key)])
async def replay(consensus_id: str) -> dict:
    try:
        row = SupabaseRepository(settings).decision_replay(consensus_id)
        if row is None:
            raise HTTPException(status_code=404, detail="decision not found")
        return row
    except PersistenceFailure as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
