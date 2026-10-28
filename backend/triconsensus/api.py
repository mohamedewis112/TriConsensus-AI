from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hmac
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .agents import AgentService
from .config import settings
from .consensus import full_consensus
from .cycle import PaperDecisionCycle, V6_PAPER_EXPERIMENT_VERSION
from .db import PersistenceFailure, SupabaseRepository
from .llm import LLMFailure, build_llm_provider
from .market_data import BybitPublicClient, MarketDataFailure
from .metrics import calculate_metrics
from .paper_monitor import PaperTradeMonitor
from .paper_runtime import PaperRuntimeFailure, PaperRuntimeService
from .runtime_gateway import RuntimeGatewayFailure
from .preflight import run_preflight
from .research_cycle import run_live_research_cycle
from .security import require_control_key
from .v51_forward import PAPER_EXPERIMENT_VERSION



class ModelSmokeResult(BaseModel):
    status: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = []

app = FastAPI(title="TriConsensus AI API", version="0.1.0")
market = BybitPublicClient(settings)


def _resolved_paper_version(version: str | None) -> str:
    if version:
        return version
    return (
        V6_PAPER_EXPERIMENT_VERSION
        if settings.v6_marl_enabled
        else PAPER_EXPERIMENT_VERSION
    )


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


@app.get("/v1/model-smoke")
async def model_smoke(request: Request) -> dict:
    if settings.app_env.lower() not in {"development", "preview", "staging"}:
        raise HTTPException(status_code=404, detail="not found")
    oidc_token = request.headers.get("x-vercel-oidc-token")
    provider = build_llm_provider(settings, vercel_oidc_token=oidc_token)
    try:
        result = await provider.structured(
            system=(
                "You are a connectivity test. Return only the requested structured schema. "
                "Do not provide financial, trading, or investment advice."
            ),
            user=(
                "Confirm that structured-output connectivity is working. "
                "Use status=OK, confidence=1.0, and one short evidence item."
            ),
            schema=ModelSmokeResult,
        )
    except LLMFailure as exc:
        raise HTTPException(status_code=503, detail=f"MODEL_UNAVAILABLE: {exc}") from exc
    return {
        "provider": settings.llm_provider,
        "model": (
            settings.vercel_ai_gateway_model
            if settings.llm_provider == "vercel_ai_gateway"
            else settings.model_id
        ),
        "structured_output": result.model_dump(),
        "paper_only": True,
        "live_trading": False,
    }


@app.get("/v1/agent-smoke")
async def agent_smoke(request: Request) -> dict:
    if settings.app_env.lower() not in {"development", "preview", "staging"}:
        raise HTTPException(status_code=404, detail="not found")
    oidc_token = request.headers.get("x-vercel-oidc-token")
    agents = AgentService(
        build_llm_provider(settings, vercel_oidc_token=oidc_token)
    )
    try:
        market_result = await agents.market_intelligence(
            {
                "scenario": "synthetic connectivity test only",
                "live_market_data": False,
                "evidence": [
                    "No live market prices were supplied.",
                    "No actionable quantitative edge was supplied.",
                ],
                "instruction": "Treat evidence as insufficient and return NEUTRAL.",
            }
        )
        proposal_result = await agents.strategist(
            {
                "scenario": "synthetic connectivity test only",
                "live_market_data": False,
                "market_intelligence": market_result.model_dump(mode="json"),
                "allowed_trade_levels": {},
                "instruction": "Choose WAIT because no approved numeric levels exist.",
            }
        )
        critic_result = await agents.risk_advocate(
            {
                "scenario": "synthetic connectivity test only",
                "live_market_data": False,
                "market_intelligence": market_result.model_dump(mode="json"),
                "proposal": proposal_result.model_dump(mode="json"),
                "instruction": "VETO any actionable position because this is a synthetic test.",
            }
        )
    except LLMFailure as exc:
        raise HTTPException(status_code=503, detail=f"AGENTS_UNAVAILABLE: {exc}") from exc

    consensus = full_consensus(market_result, proposal_result, critic_result)
    return {
        "provider": settings.llm_provider,
        "model": (
            settings.vercel_ai_gateway_model
            if settings.llm_provider == "vercel_ai_gateway"
            else settings.model_id
        ),
        "market_intelligence": market_result.model_dump(mode="json"),
        "strategist": proposal_result.model_dump(mode="json"),
        "risk_advocate": critic_result.model_dump(mode="json"),
        "consensus": consensus.model_dump(mode="json"),
        "synthetic_only": True,
        "paper_only": True,
        "live_trading": False,
        "persisted": False,
    }


@app.get("/v1/research-cycle/{symbol}")
async def research_cycle(symbol: str, request: Request) -> dict:
    if settings.app_env.lower() not in {"development", "preview", "staging"}:
        raise HTTPException(status_code=404, detail="not found")
    oidc_token = request.headers.get("x-vercel-oidc-token")
    try:
        return await run_live_research_cycle(
            settings,
            symbol=symbol,
            vercel_oidc_token=oidc_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc




def _require_cron_secret(request: Request) -> None:
    secret = os.getenv("CRON_SECRET") or ""
    auth = request.headers.get("authorization") or ""
    expected = f"Bearer {secret}"
    if not secret or not hmac.compare_digest(auth, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/v1/runtime/cron")
async def paper_runtime_cron(request: Request) -> dict:
    _require_cron_secret(request)
    if settings.trading_mode.value != "paper" or settings.v6_marl_enforcement_active:
        raise HTTPException(
            status_code=503,
            detail="paper runtime safety invariant failed",
        )

    oidc_token = request.headers.get("x-vercel-oidc-token")
    try:
        service = PaperRuntimeService(
            settings,
            vercel_oidc_token=oidc_token,
        )
        monitor_result = await service.monitor()
    except (PaperRuntimeFailure, RuntimeGatewayFailure) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"NO_ACTION: {exc}",
        ) from exc

    cycles: list[dict] = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        try:
            cycles.append(await service.run_symbol(symbol=symbol))
        except (
            PaperRuntimeFailure,
            RuntimeGatewayFailure,
            LLMFailure,
            MarketDataFailure,
            ValueError,
        ) as exc:
            cycles.append(
                {
                    "symbol": symbol,
                    "outcome": "NO_TRADE",
                    "reason": "paper_runtime_failure",
                    "error": type(exc).__name__,
                    "paper_only": True,
                    "live_trading": False,
                    "persisted": False,
                }
            )

    return {
        "status": "COMPLETED",
        "paper_only": True,
        "live_trading": False,
        "v6_enforcement": False,
        "monitor": monitor_result,
        "cycles": cycles,
        "generated_at": datetime.now(UTC).isoformat(),
    }


@app.get("/v1/runtime/status")
async def paper_runtime_status(request: Request) -> dict:
    _require_cron_secret(request)
    oidc_token = request.headers.get("x-vercel-oidc-token")
    try:
        return await PaperRuntimeService(
            settings,
            vercel_oidc_token=oidc_token,
        ).status()
    except (PaperRuntimeFailure, RuntimeGatewayFailure) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"RUNTIME_UNAVAILABLE: {exc}",
        ) from exc


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
async def paper_monitor(version: str | None = None) -> dict:
    resolved = _resolved_paper_version(version)
    try:
        return await PaperTradeMonitor(settings).run(version=resolved)
    except PersistenceFailure as exc:
        raise HTTPException(status_code=503, detail=f"NO_ACTION: {exc}") from exc


@app.get("/v1/analytics/summary", dependencies=[Depends(require_control_key)])
async def analytics_summary(version: str | None = None) -> dict:
    resolved = _resolved_paper_version(version)
    try:
        repo = SupabaseRepository(settings)
        experiment = repo.get_experiment(resolved)
        if experiment is None:
            return _empty_summary(resolved)

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
            "version": resolved,
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
async def trades(version: str | None = None, limit: int = 100) -> dict:
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    resolved = _resolved_paper_version(version)
    try:
        repo = SupabaseRepository(settings)
        experiment = repo.get_experiment(resolved)
        if experiment is None:
            return {"version": resolved, "trades": []}
        return {
            "version": resolved,
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
