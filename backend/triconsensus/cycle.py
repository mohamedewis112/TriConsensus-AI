from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json

import pandas as pd

from .account_state import build_account_state
from .agents import AgentService
from .binance_live import BinanceGlobalRatioClient, BinanceLiveContextFailure
from .config import Settings
from .db import SupabaseRepository
from .execution import PaperExecutionEngine
from .features import build_features
from .levels import build_trade_levels
from .llm import OpenRouterProvider
from .market_data import BybitPublicClient, MarketDataFailure
from .orchestrator import DecisionOrchestrator
from .v51_forward import (
    PAPER_EXPERIMENT_VERSION,
    STRATEGY_VERSION,
    V51CalibrationFailure,
    build_v51_strategy_evidence,
)
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
        self.strategy_context = BinanceGlobalRatioClient()
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
            "paper_slippage_bps": self.settings.paper_slippage_bps,
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
            "trade_level_formula": (
                "entry=last; stop=1.5ATR; tp1=minRR; tp2=1.5xTP1distance"
            ),
            "model_id": self.settings.model_id,
            "strategy_version": STRATEGY_VERSION,
            "strategy_feature": "global_long_short_change_1h",
            "strategy_relationship": "negative",
            "strategy_rule": "bottom_decile=>LONG;top_decile=>SHORT",
            "strategy_horizon_minutes": 180,
            "strategy_context_lag_minutes": 15,
            "strategy_round_trip_research_cost": 0.0015,
            "paper_exit_policy": "agent_levels_plus_deterministic_risk_engine",
            "mode": "paper",
        }

    async def run(self, symbol: str) -> dict:
        normalized = symbol.upper().replace("/", "").replace("-", "")
        symbol_map = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT"}
        if normalized not in symbol_map:
            return {"outcome": "NO_TRADE", "reason": "unsupported_symbol"}
        display_symbol = symbol_map[normalized]

        experiment = self.repo.ensure_experiment(
            version=PAPER_EXPERIMENT_VERSION,
            config=self.experiment_config(),
        )
        ledger = self.repo.list_trades(experiment["id"], limit=1000)
        account_state = build_account_state(ledger, self.settings)

        observed_at = datetime.now(UTC)
        calibration = self.repo.get_active_strategy_calibration(
            strategy_version=STRATEGY_VERSION,
            symbol=display_symbol,
            as_of=observed_at.isoformat(),
            calibration_end_on_or_before=observed_at.date().isoformat(),
        )
        if calibration is None:
            self.repo.insert(
                "system_events",
                {
                    "experiment_id": experiment["id"],
                    "severity": "warning",
                    "component": "strategy_evidence",
                    "event_type": "NO_TRADE",
                    "message": "V5.1 calibration missing or expired",
                    "payload": {"symbol": display_symbol},
                },
            )
            return {
                "outcome": "NO_TRADE",
                "reason": "strategy_calibration_missing_or_expired",
                "experiment_id": experiment["id"],
                "account_state": account_state,
            }

        try:
            live_signal = await self.strategy_context.signal(
                normalized,
                low_threshold=float(calibration["low_threshold"]),
                high_threshold=float(calibration["high_threshold"]),
                now_ms=int(observed_at.timestamp() * 1000),
            )
            strategy_evidence = build_v51_strategy_evidence(
                live_signal,
                calibration,
            )
        except (
            BinanceLiveContextFailure,
            V51CalibrationFailure,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            self.repo.insert(
                "system_events",
                {
                    "experiment_id": experiment["id"],
                    "severity": "error",
                    "component": "strategy_evidence",
                    "event_type": "NO_TRADE",
                    "message": "V5.1 evidence unavailable or invalid",
                    "payload": {
                        "symbol": display_symbol,
                        "error": type(exc).__name__,
                    },
                },
            )
            return {
                "outcome": "NO_TRADE",
                "reason": "strategy_evidence_failure",
                "experiment_id": experiment["id"],
                "account_state": account_state,
            }

        context_timestamp = datetime.fromtimestamp(
            live_signal.context_timestamp_ms / 1000,
            tz=UTC,
        )
        strategy_signal_row = self.repo.insert(
            "strategy_signal_snapshots",
            {
                "experiment_id": experiment["id"],
                "market_snapshot_id": None,
                "calibration_id": calibration["id"],
                "strategy_version": STRATEGY_VERSION,
                "symbol": display_symbol,
                "observed_at": observed_at.isoformat(),
                "context_timestamp": context_timestamp.isoformat(),
                "feature_name": live_signal.feature_name,
                "feature_value": live_signal.feature_value,
                "low_threshold": live_signal.low_threshold,
                "high_threshold": live_signal.high_threshold,
                "signal": live_signal.signal,
                "context_age_ms": live_signal.context_age_ms,
                "reason": live_signal.reason,
                "payload": strategy_evidence,
                "checksum": _checksum(strategy_evidence),
            },
        )

        if live_signal.signal == "NO_TRADE":
            return {
                "outcome": "NO_TRADE",
                "reason": "strategy_signal_no_trade",
                "experiment_id": experiment["id"],
                "strategy_calibration_id": calibration["id"],
                "strategy_signal_snapshot_id": strategy_signal_row["id"],
                "strategy_evidence": strategy_evidence,
                "account_state": account_state,
            }

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

        timeframe_features: dict[str, dict] = {}
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
                "feature_version": "v1.4.0",
                "features": {
                    "primary": primary,
                    "timeframes": timeframe_features,
                    "allowed_trade_levels": asdict(allowed_levels),
                },
                "leakage_guard": {
                    "open_candle_excluded": True,
                    "ordered_oldest_to_newest": True,
                    "llm_numeric_levels_restricted": True,
                    "account_state_server_derived": True,
                },
            },
        )

        market_context = {
            "ticker": snapshot["ticker"],
            "orderbook": snapshot["orderbook"],
            "market_data_age_ms": snapshot["market_data_age_ms"],
            "timeframes": timeframe_features,
            "strategy_evidence": strategy_evidence,
        }
        result = await self.orchestrator.decide(
            symbol=display_symbol,
            market_context=market_context,
            quant_features=primary,
            ml_evidence=strategy_evidence,
            account_state=account_state,
            allowed_levels=allowed_levels,
        )

        agent_ids: dict[str, str] = {}
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
                    "prompt_version": "v1.4",
                    "input_context_hash": _checksum(
                        {
                            "market": market_context,
                            "features": primary,
                            "strategy_evidence": strategy_evidence,
                            "allowed_levels": asdict(allowed_levels),
                            "account_state": account_state,
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
                    "is_valid": (
                        result["reason"]
                        != "strategist_used_unapproved_numeric_levels"
                    ),
                },
            )
            agent_ids[role] = row["id"]

        consensus_outcome = str(result.get("consensus_outcome") or "NO_TRADE")
        full_consensus = bool(result.get("full_consensus", False))
        consensus = self.repo.insert(
            "consensus_decisions",
            {
                "experiment_id": experiment["id"],
                "market_snapshot_id": market_row["id"],
                "market_agent_run_id": agent_ids.get("market_intelligence"),
                "strategist_agent_run_id": agent_ids.get("trade_strategist"),
                "risk_agent_run_id": agent_ids.get("risk_advocate"),
                "outcome": consensus_outcome,
                "full_consensus": full_consensus,
                "reason": result.get("consensus_reason") or result["reason"],
            },
        )

        risk_row = None
        risk = result.get("risk")
        proposal = (result.get("agents") or {}).get("proposal")
        if risk is not None and proposal is not None:
            entry = sum(proposal["entry_zone"]) / len(proposal["entry_zone"])
            risk_row = self.repo.insert(
                "risk_evaluations",
                {
                    "experiment_id": experiment["id"],
                    "consensus_decision_id": consensus["id"],
                    "approved": bool(risk["approved"]),
                    "checks": {
                        "reason": risk["reason"],
                        "numeric_levels_validated": True,
                        "full_consensus": full_consensus,
                        "account_state": account_state,
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
                    "slippage_bps": account_state["slippage_bps"],
                    "rejection_reason": (
                        None if risk["approved"] else risk["reason"]
                    ),
                },
            )

        if result["outcome"] == "NO_TRADE":
            return {
                **result,
                "experiment_id": experiment["id"],
                "market_snapshot_id": market_row["id"],
                "feature_snapshot_id": feature_row["id"],
                "consensus_id": consensus["id"],
                "risk_evaluation_id": risk_row["id"] if risk_row else None,
                "strategy_calibration_id": calibration["id"],
                "strategy_signal_snapshot_id": strategy_signal_row["id"],
                "strategy_evidence": strategy_evidence,
                "account_state": account_state,
            }

        if risk_row is None or proposal is None:
            return {
                "outcome": "NO_TRADE",
                "reason": "missing_risk_audit",
                "experiment_id": experiment["id"],
                "consensus_id": consensus["id"],
                "strategy_calibration_id": calibration["id"],
                "strategy_signal_snapshot_id": strategy_signal_row["id"],
                "strategy_evidence": strategy_evidence,
                "account_state": account_state,
            }

        entry = sum(proposal["entry_zone"]) / len(proposal["entry_zone"])
        fill = self.execution.open_market(
            side=result["outcome"],
            requested_price=entry,
            quantity=float(risk["position_size"]),
            slippage_bps=float(account_state["slippage_bps"]),
        )
        opened_at = datetime.now(UTC)
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
                "opened_at": opened_at.isoformat(),
            },
        )
        self.repo.insert(
            "trade_events",
            {
                "experiment_id": experiment["id"],
                "trade_id": trade["id"],
                "event_type": "PAPER_OPEN",
                "event_time": opened_at.isoformat(),
                "payload": {
                    **asdict(fill),
                    "account_state_at_entry": account_state,
                },
            },
        )

        return {
            **result,
            "experiment_id": experiment["id"],
            "market_snapshot_id": market_row["id"],
            "feature_snapshot_id": feature_row["id"],
            "consensus_id": consensus["id"],
            "risk_evaluation_id": risk_row["id"],
            "trade_id": trade["id"],
            "paper_fill": asdict(fill),
            "strategy_calibration_id": calibration["id"],
            "strategy_signal_snapshot_id": strategy_signal_row["id"],
            "strategy_evidence": strategy_evidence,
            "account_state": account_state,
        }
