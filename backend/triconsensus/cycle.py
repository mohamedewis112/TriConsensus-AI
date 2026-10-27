from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json

import pandas as pd

from .agents import AgentService
from .config import Settings
from .db import SupabaseRepository
from .execution import PaperExecutionEngine
from .features import build_features
from .levels import build_trade_levels
from .llm import OpenRouterProvider
from .market_data import BybitPublicClient, MarketDataFailure
from .ml_evidence import neutral_ml_evidence
from .orchestrator import DecisionOrchestrator
from .risk import DeterministicRiskEngine


def _frame(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candles)[["open", "high", "low", "close", "volume"]]


def _checksum(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


class PaperDecisionCycle:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.market = BybitPublicClient(settings)
        self.repo = SupabaseRepository(settings)
        self.agents = AgentService(OpenRouterProvider(settings))
        self.orchestrator = DecisionOrchestrator(
            self.agents,
            DeterministicRiskEngine(settings),
        )
        self.execution = PaperExecutionEngine()

    def experiment_config(self) -> dict:
        return {
            "symbols": ["BTC/USDT", "ETH/USDT"],
            "market": "USDT_PERPETUAL",
            "primary_timeframe": "15m",
            "context_timeframes": ["1m", "5m", "15m", "1h", "4h"],
            "risk_per_trade": self.settings.risk_per_trade,
            "max_daily_loss": self.settings.max_daily_loss,
            "max_drawdown": self.settings.max_drawdown,
            "max_concurrent_trades": self.settings.max_concurrent_trades,
            "max_account_exposure": self.settings.max_account_exposure,
            "max_correlated_exposure": self.settings.max_correlated_exposure,
            "max_losing_streak": self.settings.max_losing_streak,
            "cooldown_after_loss_minutes": self.settings.cooldown_after_loss_minutes,
            "min_risk_reward": self.settings.min_risk_reward,
            "max_spread_bps": self.settings.max_spread_bps,
            "max_slippage_bps": self.settings.max_slippage_bps,
            "max_realized_volatility_20": self.settings.max_realized_volatility_20,
            "trade_level_formula": "entry=last; stop=1.5ATR; tp1=minRR; tp2=1.5xTP1distance",
            "model_id": self.settings.model_id,
            "mode": "paper",
        }

    async def run(self, symbol: str, account_state: dict) -> dict:
        normalized = symbol.upper().replace("/", "").replace("-", "")
        symbol_map = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT"}
        if normalized not in symbol_map:
            return {"outcome": "NO_TRADE", "reason": "unsupported_symbol"}
        display_symbol = symbol_map[normalized]

        experiment = self.repo.ensure_experiment(
            version="V1",
            config=self.experiment_config(),
        )

        try:
            snapshot = await self.market.multi_timeframe_snapshot(normalized)
        except (MarketDataFailure, ValueError) as exc:
            self.repo.insert(
                "system_events",
                {
                    "experiment_id": experiment["id"],
                    "severity": "error",
                    "component": "market_data",
                    "event_type": "NO_TRADE",
                    "message": "market data unavailable",
                    "payload": {"error": type(exc).__name__},
                },
            )
            return {"outcome": "NO_TRADE", "reason": "market_data_failure"}

        market_row = self.repo.insert(
            "market_snapshots",
            {
                "experiment_id": experiment["id"],
                "symbol": display_symbol,
                "primary_timeframe": "15m",
                "captured_at": snapshot["ticker"]["captured_at"],
                "source": "bybit",
                "market_data": snapshot,
                "orderbook": snapshot["orderbook"],
                "funding_rate": snapshot["ticker"]["funding_rate"],
                "open_interest": snapshot["ticker"]["open_interest"],
                "market_data_age_ms": snapshot["market_data_age_ms"],
                "checksum": _checksum(snapshot),
            },
        )

        timeframe_features = {}
        for timeframe, candles in snapshot["candles"].items():
            timeframe_features[timeframe] = build_features(
                _frame(candles),
                bid=snapshot["ticker"]["bid"],
                ask=snapshot["ticker"]["ask"],
                orderbook=snapshot["orderbook"],
            )

        primary = timeframe_features["15m"]
        allowed_levels = build_trade_levels(
            last_price=float(snapshot["ticker"]["last_price"]),
            atr=float(primary["atr_14"]),
            min_risk_reward=self.settings.min_risk_reward,
        )

        feature_row = self.repo.insert(
            "feature_snapshots",
            {
                "experiment_id": experiment["id"],
                "market_snapshot_id": market_row["id"],
                "symbol": display_symbol,
                "feature_version": "v1.3.0",
                "features": {
                    "primary": primary,
                    "timeframes": timeframe_features,
                    "allowed_trade_levels": asdict(allowed_levels),
                },
                "leakage_guard": {
                    "open_candle_excluded": True,
                    "ordered_oldest_to_newest": True,
                    "llm_numeric_levels_restricted": True,
                },
            },
        )

        ml = neutral_ml_evidence()
        self.repo.insert(
            "ml_predictions",
            {
                "experiment_id": experiment["id"],
                "market_snapshot_id": market_row["id"],
                "model_family": ml.model_family,
                "model_version": "untrained-v1",
                "target": "evidence_bundle",
                "prediction": asdict(ml),
                "confidence": 0.0,
            },
        )

        market_context = {
            "ticker": snapshot["ticker"],
            "orderbook": snapshot["orderbook"],
            "market_data_age_ms": snapshot["market_data_age_ms"],
            "timeframes": timeframe_features,
        }
        result = await self.orchestrator.decide(
            symbol=display_symbol,
            market_context=market_context,
            quant_features=primary,
            ml_evidence=asdict(ml),
            account_state=account_state,
            allowed_levels=allowed_levels,
        )

        agent_ids = {}
        for role, key in [
            ("market_intelligence", "market"),
            ("trade_strategist", "proposal"),
            ("risk_advocate", "critic"),
        ]:
            output = (result.get("agents") or {}).get(key)
            if output is None:
                continue
            row = self.repo.insert(
                "agent_runs",
                {
                    "experiment_id": experiment["id"],
                    "market_snapshot_id": market_row["id"],
                    "agent_role": role,
                    "provider": "openrouter",
                    "model_id": self.settings.model_id,
                    "prompt_version": "v1.2",
                    "input_context_hash": _checksum(
                        {
                            "market": market_context,
                            "features": primary,
                            "ml": asdict(ml),
                            "allowed_levels": asdict(allowed_levels),
                        }
                    ),
                    "output_json": output,
                    "decision": (
                        output.get("bias")
                        or output.get("decision")
                        or output.get("verdict")
                    ),
                    "confidence": output.get("confidence"),
                    "evidence": output.get("evidence") or [],
                    "is_valid": result["reason"] != "strategist_used_unapproved_numeric_levels",
                },
            )
            agent_ids[role] = row["id"]

        full_consensus = result["outcome"] in {"LONG", "SHORT"} and result.get("risk") is not None
        consensus = self.repo.insert(
            "consensus_decisions",
            {
                "experiment_id": experiment["id"],
                "market_snapshot_id": market_row["id"],
                "market_agent_run_id": agent_ids.get("market_intelligence"),
                "strategist_agent_run_id": agent_ids.get("trade_strategist"),
                "risk_agent_run_id": agent_ids.get("risk_advocate"),
                "outcome": result["outcome"] if result["outcome"] in {"LONG", "SHORT"} else "NO_TRADE",
                "full_consensus": full_consensus,
                "reason": result["reason"],
            },
        )

        if result["outcome"] == "NO_TRADE":
            return {
                **result,
                "experiment_id": experiment["id"],
                "market_snapshot_id": market_row["id"],
                "feature_snapshot_id": feature_row["id"],
                "consensus_id": consensus["id"],
            }

        proposal = result["agents"]["proposal"]
        entry = sum(proposal["entry_zone"]) / len(proposal["entry_zone"])
        risk = result["risk"]
        risk_row = self.repo.insert(
            "risk_evaluations",
            {
                "experiment_id": experiment["id"],
                "consensus_decision_id": consensus["id"],
                "approved": True,
                "checks": {
                    "reason": risk["reason"],
                    "numeric_levels_validated": True,
                    "full_consensus": True,
                    "kill_switch_active": account_state.get("kill_switch_active", False),
                    "losing_streak": account_state.get("losing_streak", 0),
                },
                "proposed_entry": entry,
                "stop_loss": proposal["stop_loss"],
                "take_profit_1": proposal["take_profit_1"],
                "take_profit_2": proposal.get("take_profit_2"),
                "risk_reward": risk["risk_reward"],
                "risk_fraction": self.settings.risk_per_trade,
                "position_size": risk["position_size"],
                "max_exposure_after": risk["projected_exposure"],
                "spread_bps": primary.get("spread_bps"),
                "slippage_bps": account_state.get("slippage_bps", 2),
            },
        )

        fill = self.execution.open_market(
            side=result["outcome"],
            requested_price=entry,
            quantity=risk["position_size"],
            slippage_bps=float(account_state.get("slippage_bps", 2)),
        )
        trade = self.repo.insert(
            "paper_trades",
            {
                "experiment_id": experiment["id"],
                "consensus_decision_id": consensus["id"],
                "risk_evaluation_id": risk_row["id"],
                "symbol": display_symbol,
                "side": result["outcome"],
                "status": "open",
                "requested_entry": entry,
                "actual_entry": fill.fill_price,
                "stop_loss": proposal["stop_loss"],
                "take_profit_1": proposal["take_profit_1"],
                "take_profit_2": proposal.get("take_profit_2"),
                "position_size": fill.quantity,
                "notional_value": fill.fill_price * fill.quantity,
                "estimated_fees": fill.fee,
                "realized_fees": fill.fee,
                "estimated_slippage": fill.slippage_cost,
                "realized_slippage": fill.slippage_cost,
                "opened_at": datetime.now(UTC).isoformat(),
            },
        )
        self.repo.insert(
            "trade_events",
            {
                "experiment_id": experiment["id"],
                "trade_id": trade["id"],
                "event_type": "PAPER_OPEN",
                "event_time": datetime.now(UTC).isoformat(),
                "payload": asdict(fill),
            },
        )

        return {
            **result,
            "experiment_id": experiment["id"],
            "market_snapshot_id": market_row["id"],
            "feature_snapshot_id": feature_row["id"],
            "consensus_id": consensus["id"],
            "trade_id": trade["id"],
            "paper_fill": asdict(fill),
        }
