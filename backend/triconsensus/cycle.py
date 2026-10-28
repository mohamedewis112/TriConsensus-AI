from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
import math

import pandas as pd

from .account_state import build_account_state
from .agents import AgentService
from .binance_live import (
    BinanceGlobalRatioClient,
    BinanceLiveContextFailure,
    GlobalLongShortPoint,
    aggregate_5m_ratio_to_15m,
)
from .config import Settings
from .db import PersistenceFailure, SupabaseRepository
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
from .v6_agents import DynamicVolatilityRiskAgent, MetaRLPortfolioAdapterAgent
from .v6_marl import MAPPOPolicyBundle, MARLDependencyError
from .v6_models import RewardMemoryRecord, VolatilityRiskState
from .v6_reward import build_portfolio_state


def _frame(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candles)[["open", "high", "low", "close", "volume"]]


def _checksum(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


V6_PAPER_EXPERIMENT_VERSION = "V6-PAPER-SHADOW"


class PaperDecisionCycle:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.market = BybitPublicClient(settings)
        self.strategy_context = BinanceGlobalRatioClient()
        self.repo = SupabaseRepository(settings)
        self.agents = AgentService(OpenRouterProvider(settings))

        self.v6_policy = None
        self.portfolio_adapter = None
        self.dynamic_risk_agent = None
        if settings.v6_marl_enabled:
            self.v6_policy = MAPPOPolicyBundle(
                portfolio_input_dim=15,
                risk_input_dim=8,
                global_state_dim=23,
            )
            try:
                policy_row = self.repo.latest_marl_policy("mappo_shared")
                if policy_row and policy_row.get("weights"):
                    self.v6_policy.load_payload(
                        policy_row["weights"],
                        policy_version=str(policy_row["version"]),
                    )
            except (PersistenceFailure, MARLDependencyError, ValueError, KeyError):
                # Shadow mode is allowed to continue with a neutral/untrained policy.
                # Enforcement will fail closed inside the adaptive agents.
                pass

            self.portfolio_adapter = MetaRLPortfolioAdapterAgent(
                self.v6_policy,
                shadow_mode=settings.v6_marl_shadow_mode,
                blacklist_probability=settings.v6_blacklist_probability,
                minimum_blacklist_samples=settings.v6_min_blacklist_samples,
                hard_losing_streak=settings.v6_hard_losing_streak,
                hard_mean_net_return=settings.v6_hard_mean_net_return,
            )
            self.dynamic_risk_agent = DynamicVolatilityRiskAgent(
                self.v6_policy,
                shadow_mode=settings.v6_marl_shadow_mode,
                max_microstructure_age_ms=settings.v6_max_microstructure_age_ms,
                adverse_imbalance=settings.v6_adverse_orderbook_imbalance,
            )

        self.orchestrator = DecisionOrchestrator(
            self.agents,
            DeterministicRiskEngine(settings),
            portfolio_adapter=self.portfolio_adapter,
            dynamic_risk_agent=self.dynamic_risk_agent,
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
            **(
                {
                    "adaptive_layer": {
                        "version": "V6",
                        "architecture": "constrained_mappo_neuro_symbolic",
                        "shadow_mode": self.settings.v6_marl_shadow_mode,
                        "reward_window": self.settings.v6_reward_window,
                        "warmup_trades_per_asset": self.settings.v6_min_warmup_trades_per_asset,
                        "drawdown_penalty": self.settings.v6_drawdown_penalty,
                        "risk_engine_authority": "deterministic_veto_supreme",
                    }
                }
                if self.settings.v6_marl_enabled
                else {}
            ),
        }

    async def run(self, symbol: str) -> dict:
        normalized = symbol.upper().replace("/", "").replace("-", "")
        symbol_map = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT"}
        if normalized not in symbol_map:
            return {"outcome": "NO_TRADE", "reason": "unsupported_symbol"}
        display_symbol = symbol_map[normalized]

        experiment = self.repo.ensure_experiment(
            version=(
                V6_PAPER_EXPERIMENT_VERSION
                if self.settings.v6_marl_enabled
                else PAPER_EXPERIMENT_VERSION
            ),
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
            ratio_rows = self.repo.recent_binance_global_ratio(
                symbol=normalized,
                observed_at_or_before=observed_at.isoformat(),
                limit=96,
            )
            raw_points = [
                GlobalLongShortPoint(
                    timestamp_ms=int(
                        datetime.fromisoformat(
                            str(row["observed_at"]).replace("Z", "+00:00")
                        ).timestamp()
                        * 1000
                    ),
                    ratio=float(row["long_short_ratio"]),
                    long_account=float(row["long_account"]),
                    short_account=float(row["short_account"]),
                )
                for row in ratio_rows
            ]
            live_points = aggregate_5m_ratio_to_15m(raw_points)
            live_signal = self.strategy_context.signal_from_points(
                normalized,
                live_points,
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
            PersistenceFailure,
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

        adaptive_context: dict = {}
        if self.settings.v6_marl_enabled:
            reward_records: list[RewardMemoryRecord] = []
            try:
                for row in self.repo.recent_marl_reward_memory(
                    limit=self.settings.v6_reward_window * 2
                ):
                    reward_records.append(
                        RewardMemoryRecord(
                            symbol=row["symbol"],
                            trade_id=str(row["trade_id"]),
                            policy_version=str(row["policy_version"]),
                            state=row.get("state_json") or {},
                            action=row.get("action_json") or {},
                            net_return=float(row["net_return"]),
                            max_drawdown=float(row["max_drawdown"]),
                            reward=float(row["reward"]),
                            terminal=bool(row.get("terminal", True)),
                            created_at=row.get("created_at"),
                        )
                    )
            except (PersistenceFailure, KeyError, TypeError, ValueError):
                reward_records = []

            portfolio_state = build_portfolio_state(
                reward_records,
                window=self.settings.v6_reward_window,
                minimum_warmup_trades_per_asset=(
                    self.settings.v6_min_warmup_trades_per_asset
                ),
                as_of=observed_at,
            )

            implied_volatility = None
            try:
                iv_snapshot = await self.market.implied_volatility(normalized)
                implied_volatility = float(iv_snapshot["mark_iv"])
                try:
                    self.repo.insert(
                        "v6_volatility_snapshots",
                        {
                            "symbol": display_symbol,
                            "source": str(iv_snapshot["source"]),
                            "captured_at": str(iv_snapshot["captured_at"]),
                            "mark_iv": implied_volatility,
                            "expiry": iv_snapshot.get("expiry"),
                            "sample_count": int(iv_snapshot.get("sample_count") or 0),
                            "payload": iv_snapshot,
                        },
                    )
                except PersistenceFailure:
                    if not self.settings.v6_marl_shadow_mode:
                        implied_volatility = None
                        iv_snapshot = None
            except (MarketDataFailure, KeyError, TypeError, ValueError):
                iv_snapshot = None

            bybit_imbalance = float(snapshot["orderbook"]["imbalance"])
            binance_imbalance = None
            microstructure_age_ms = None
            try:
                micro_rows = self.repo.recent_v6_microstructure(
                    symbol=display_symbol,
                    limit=20,
                )
                latest_by_venue: dict[str, dict] = {}
                for row in micro_rows:
                    latest_by_venue[str(row["venue"]).lower()] = row
                bybit_row = latest_by_venue.get("bybit")
                binance_row = latest_by_venue.get("binance")
                if bybit_row is not None:
                    bybit_imbalance = float(bybit_row["imbalance"])
                if binance_row is not None:
                    binance_imbalance = float(binance_row["imbalance"])
                if bybit_row is not None and binance_row is not None:
                    latest_capture = min(
                        datetime.fromisoformat(
                            str(bybit_row["captured_at"]).replace("Z", "+00:00")
                        ),
                        datetime.fromisoformat(
                            str(binance_row["captured_at"]).replace("Z", "+00:00")
                        ),
                    )
                    microstructure_age_ms = max(
                        0,
                        int((observed_at - latest_capture).total_seconds() * 1000),
                    )
            except (PersistenceFailure, KeyError, TypeError, ValueError):
                pass

            # build_features stores a 20-bar scaled volatility. Convert it back
            # to 15m sigma then annualize before comparing it with option IV.
            realized_annualized = float(primary["realized_vol_20"]) * math.sqrt(
                (365 * 24 * 4) / 20
            )
            volatility_state = VolatilityRiskState(
                symbol=display_symbol,
                side=live_signal.signal,
                entry_price=float(snapshot["ticker"]["last_price"]),
                atr_14=float(primary["atr_14"]),
                realized_volatility=max(0.0, realized_annualized),
                implied_volatility=implied_volatility,
                bybit_orderbook_imbalance=bybit_imbalance,
                binance_orderbook_imbalance=binance_imbalance,
                spread_bps=float(primary.get("spread_bps") or 0),
                market_regime=(
                    f"{primary.get('trend', 'unknown')}|"
                    f"adx={float(primary.get('adx_14') or 0):.2f}"
                ),
                microstructure_age_ms=microstructure_age_ms,
            )
            adaptive_context = {
                "portfolio_state": portfolio_state.model_dump(mode="json"),
                "volatility_state": volatility_state.model_dump(mode="json"),
                "iv_snapshot": iv_snapshot,
            }

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
            adaptive_context=adaptive_context,
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
                        "v6_adaptive": result.get("v6_adaptive") or {},
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

        if self.settings.v6_marl_enabled:
            try:
                for role, action in (result.get("v6_adaptive") or {}).items():
                    self.repo.insert(
                        "marl_action_logs",
                        {
                            "experiment_id": experiment["id"],
                            "market_snapshot_id": market_row["id"],
                            "signal_snapshot_id": strategy_signal_row["id"],
                            "agent_role": role,
                            "policy_version": str(
                                action.get("policy_version") or "unknown"
                            ),
                            "state_json": adaptive_context,
                            "action_json": action,
                            "projected_action_json": action,
                            "shadow_mode": bool(action.get("shadow_mode", True)),
                            "vetoed_by_risk": bool(
                                result.get("risk")
                                and not result["risk"].get("approved", False)
                            ),
                        },
                    )
            except PersistenceFailure:
                if not self.settings.v6_marl_shadow_mode:
                    return {
                        "outcome": "NO_TRADE",
                        "reason": "v6_adaptive_audit_failure",
                        "experiment_id": experiment["id"],
                        "strategy_signal_snapshot_id": strategy_signal_row["id"],
                    }

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
                    "v6_adaptive_context": adaptive_context,
                    "v6_adaptive": result.get("v6_adaptive") or {},
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
