from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import math

import pandas as pd

from .config import Settings
from .db import PersistenceFailure, SupabaseRepository
from .execution import PaperExecutionEngine
from .features import build_features
from .market_data import BybitPublicClient, MarketDataFailure
from .v51_forward import PAPER_EXPERIMENT_VERSION
from .v6_agents import DynamicVolatilityRiskAgent
from .v6_marl import MAPPOPolicyBundle, MARLDependencyError
from .v6_models import DynamicRiskAction, VolatilityRiskState
from .v6_reward import reward_value


@dataclass(frozen=True)
class ExitSignal:
    triggered: bool
    reason: str | None = None


def evaluate_exit(
    trade: dict,
    last_price: float,
    *,
    mfe: float | None = None,
    trailing_lock_fraction: float = 0.0,
) -> ExitSignal:
    side = str(trade["side"])
    stop = float(trade["stop_loss"])
    tp1 = float(trade["take_profit_1"])

    if side == "LONG":
        if last_price <= stop:
            return ExitSignal(True, "STOP_LOSS")
        if last_price >= tp1:
            return ExitSignal(True, "TAKE_PROFIT_1")
    elif side == "SHORT":
        if last_price >= stop:
            return ExitSignal(True, "STOP_LOSS")
        if last_price <= tp1:
            return ExitSignal(True, "TAKE_PROFIT_1")
    else:
        return ExitSignal(True, "INVALID_SIDE")

    if trailing_lock_fraction > 0 and mfe is not None and mfe > 0:
        entry = float(trade["actual_entry"])
        quantity = float(trade["position_size"])
        initial_risk = abs(entry - stop) * quantity
        current_profit = current_excursion(trade, last_price)
        if (
            initial_risk > 0
            and mfe >= initial_risk
            and current_profit <= (mfe * trailing_lock_fraction)
        ):
            return ExitSignal(True, "TRAILING_PROFIT")

    return ExitSignal(False)


def current_excursion(trade: dict, last_price: float) -> float:
    entry = float(trade["actual_entry"])
    quantity = float(trade["position_size"])
    if trade["side"] == "LONG":
        return (last_price - entry) * quantity
    if trade["side"] == "SHORT":
        return (entry - last_price) * quantity
    raise ValueError("invalid side")


def update_excursions(trade: dict, last_price: float) -> tuple[float, float]:
    excursion = current_excursion(trade, last_price)
    old_mae = float(trade.get("mae") or 0)
    old_mfe = float(trade.get("mfe") or 0)
    return min(old_mae, excursion, 0.0), max(old_mfe, excursion, 0.0)


def duration_seconds(opened_at: str, closed_at: datetime) -> int:
    opened = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=UTC)
    return max(0, int((closed_at - opened.astimezone(UTC)).total_seconds()))


def horizon_expired(
    opened_at: str,
    now: datetime,
    *,
    horizon_minutes: int,
) -> bool:
    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")
    return duration_seconds(opened_at, now) >= horizon_minutes * 60


def _frame(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candles)[["open", "high", "low", "close", "volume"]]


def project_live_levels(
    trade: dict,
    *,
    atr: float,
    action: DynamicRiskAction,
    minimum_risk_reward: float,
) -> tuple[float, float]:
    """Project a fresh V6 risk action without ever widening post-entry loss."""
    if atr <= 0:
        raise ValueError("ATR must be positive")
    entry = float(trade["actual_entry"])
    old_stop = float(trade["stop_loss"])
    old_tp = float(trade["take_profit_1"])
    stop_distance = atr * float(action.stop_atr_multiplier)
    rr = max(
        minimum_risk_reward,
        minimum_risk_reward * float(action.reward_to_risk_multiplier),
    )
    target_distance = stop_distance * rr

    if trade["side"] == "LONG":
        candidate_stop = entry - stop_distance
        candidate_tp = entry + target_distance
        stop = max(old_stop, candidate_stop)
        tp = min(old_tp, candidate_tp)
        if not (stop < entry < tp):
            return old_stop, old_tp
        return stop, tp

    if trade["side"] == "SHORT":
        candidate_stop = entry + stop_distance
        candidate_tp = entry - target_distance
        stop = min(old_stop, candidate_stop)
        tp = max(old_tp, candidate_tp)
        if not (tp < entry < stop):
            return old_stop, old_tp
        return stop, tp

    raise ValueError("invalid side")


class PaperTradeMonitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo = SupabaseRepository(settings)
        self.market = BybitPublicClient(settings)
        self.execution = PaperExecutionEngine()

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
            pass

        self.v6_risk_agent = DynamicVolatilityRiskAgent(
            self.v6_policy,
            shadow_mode=settings.v6_marl_shadow_mode,
            max_microstructure_age_ms=settings.v6_max_microstructure_age_ms,
            adverse_imbalance=settings.v6_adverse_orderbook_imbalance,
        )

    async def _fresh_v6_risk_overlay(
        self,
        *,
        trade: dict,
        bybit_symbol: str,
        display_symbol: str,
        ticker: dict,
    ) -> tuple[dict, DynamicRiskAction, VolatilityRiskState] | None:
        observed_at = datetime.now(UTC)
        try:
            candles = await self.market.klines(
                bybit_symbol,
                "15m",
                limit=60,
                drop_open_candle=True,
            )
            features = build_features(_frame(candles))
        except (MarketDataFailure, ValueError, KeyError, TypeError):
            return None

        implied_volatility = None
        try:
            iv_snapshot = await self.market.implied_volatility(bybit_symbol)
            implied_volatility = float(iv_snapshot["mark_iv"])
        except (MarketDataFailure, KeyError, TypeError, ValueError):
            pass

        latest_by_venue: dict[str, dict] = {}
        try:
            for row in self.repo.recent_v6_microstructure(
                symbol=display_symbol,
                limit=20,
            ):
                latest_by_venue[str(row["venue"]).lower()] = row
        except PersistenceFailure:
            latest_by_venue = {}

        bybit_row = latest_by_venue.get("bybit")
        binance_row = latest_by_venue.get("binance")
        bybit_imbalance = (
            float(bybit_row["imbalance"]) if bybit_row is not None else None
        )
        binance_imbalance = (
            float(binance_row["imbalance"]) if binance_row is not None else None
        )
        microstructure_age_ms = None
        if bybit_row is not None and binance_row is not None:
            try:
                oldest_capture = min(
                    datetime.fromisoformat(
                        str(bybit_row["captured_at"]).replace("Z", "+00:00")
                    ),
                    datetime.fromisoformat(
                        str(binance_row["captured_at"]).replace("Z", "+00:00")
                    ),
                )
                microstructure_age_ms = max(
                    0,
                    int((observed_at - oldest_capture).total_seconds() * 1000),
                )
            except (TypeError, ValueError):
                microstructure_age_ms = None

        bid = float(ticker.get("bid") or 0)
        ask = float(ticker.get("ask") or 0)
        if bybit_row is not None:
            spread_bps = float(bybit_row["spread_bps"])
        elif bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread_bps = ((ask - bid) / mid) * 10_000 if mid > 0 else 0.0
        else:
            spread_bps = 0.0

        realized_annualized = float(features["realized_vol_20"]) * math.sqrt(
            (365 * 24 * 4) / 20
        )
        state = VolatilityRiskState(
            symbol=display_symbol,
            side=str(trade["side"]),
            entry_price=float(trade["actual_entry"]),
            atr_14=float(features["atr_14"]),
            realized_volatility=max(0.0, realized_annualized),
            implied_volatility=implied_volatility,
            bybit_orderbook_imbalance=bybit_imbalance,
            binance_orderbook_imbalance=binance_imbalance,
            spread_bps=max(0.0, spread_bps),
            market_regime=(
                f"{features.get('trend', 'unknown')}|"
                f"adx={float(features.get('adx_14') or 0):.2f}"
            ),
            microstructure_age_ms=microstructure_age_ms,
        )
        action = self.v6_risk_agent.decide(state)

        trade_view = dict(trade)
        if not action.shadow_mode and not action.force_no_trade:
            stop_loss, take_profit_1 = project_live_levels(
                trade,
                atr=state.atr_14,
                action=action,
                minimum_risk_reward=self.settings.min_risk_reward,
            )
            trade_view["stop_loss"] = stop_loss
            trade_view["take_profit_1"] = take_profit_1

        return trade_view, action, state

    async def run(self, *, version: str = PAPER_EXPERIMENT_VERSION) -> dict:
        experiment = self.repo.get_experiment(version)
        if experiment is None:
            return {"version": version, "checked": 0, "closed": 0, "results": []}

        open_trades = self.repo.list_trades(
            experiment["id"],
            status="open",
            limit=100,
        )
        tickers: dict[str, dict] = {}
        results: list[dict] = []

        for trade in reversed(open_trades):
            display_symbol = str(trade["symbol"])
            bybit_symbol = display_symbol.replace("/", "")
            try:
                if bybit_symbol not in tickers:
                    tickers[bybit_symbol] = await self.market.ticker(bybit_symbol)
                ticker = tickers[bybit_symbol]
            except (MarketDataFailure, ValueError) as exc:
                self.repo.insert(
                    "system_events",
                    {
                        "experiment_id": experiment["id"],
                        "severity": "error",
                        "component": "paper_monitor",
                        "event_type": "NO_ACTION",
                        "message": "market data unavailable while monitoring open trade",
                        "payload": {
                            "trade_id": trade["id"],
                            "error": type(exc).__name__,
                        },
                    },
                )
                results.append(
                    {
                        "trade_id": trade["id"],
                        "status": "OPEN",
                        "action": "NO_ACTION",
                        "reason": "market_data_failure",
                    }
                )
                continue

            last_price = float(ticker["last_price"])
            mae, mfe = update_excursions(trade, last_price)

            open_payload: dict = {}
            trailing_lock_fraction = 0.0
            if version.startswith("V6"):
                try:
                    open_events = [
                        event
                        for event in self.repo.trade_events_for_trade(trade["id"])
                        if event.get("event_type") == "PAPER_OPEN"
                    ]
                    open_payload = (
                        (open_events[0].get("payload") or {}) if open_events else {}
                    )
                except PersistenceFailure:
                    open_payload = {}
                dynamic_action = (
                    (open_payload.get("v6_adaptive") or {}).get("dynamic_risk") or {}
                )
                if not bool(dynamic_action.get("shadow_mode", True)):
                    trailing_lock_fraction = max(
                        0.0,
                        min(
                            0.90,
                            float(dynamic_action.get("trailing_lock_fraction") or 0),
                        ),
                    )

            elapsed_seconds = duration_seconds(str(trade["opened_at"]), datetime.now(UTC))
            if (
                version.startswith("V6")
                and horizon_expired(
                    str(trade["opened_at"]),
                    datetime.now(UTC),
                    horizon_minutes=self.settings.v6_trade_horizon_minutes,
                )
            ):
                signal = ExitSignal(True, "HORIZON_LIMIT")
            else:
                signal = evaluate_exit(
                    trade,
                    last_price,
                    mfe=mfe,
                    trailing_lock_fraction=trailing_lock_fraction,
                )

            if not signal.triggered:
                self.repo.update_trade(trade["id"], {"mae": mae, "mfe": mfe})
                results.append(
                    {
                        "trade_id": trade["id"],
                        "status": "OPEN",
                        "action": "MONITORED",
                        "last_price": last_price,
                    }
                )
                continue

            if signal.reason == "INVALID_SIDE":
                results.append(
                    {
                        "trade_id": trade["id"],
                        "status": "OPEN",
                        "action": "NO_ACTION",
                        "reason": "invalid_side",
                    }
                )
                continue

            slippage_bps = min(
                self.settings.paper_slippage_bps,
                self.settings.max_slippage_bps,
            )
            fill = self.execution.close_market(
                position_side=str(trade["side"]),
                requested_price=last_price,
                quantity=float(trade["position_size"]),
                slippage_bps=slippage_bps,
            )

            total_fees = float(trade.get("realized_fees") or 0) + fill.fee
            total_slippage = float(trade.get("realized_slippage") or 0) + fill.slippage_cost
            pnl = self.execution.pnl(
                position_side=str(trade["side"]),
                entry_fill=float(trade["actual_entry"]),
                exit_fill=fill.fill_price,
                quantity=float(trade["position_size"]),
                total_fees=total_fees,
            )
            closed_at = datetime.now(UTC)
            updated = self.repo.update_trade(
                trade["id"],
                {
                    "status": "closed",
                    "requested_exit": last_price,
                    "actual_exit": fill.fill_price,
                    "realized_fees": total_fees,
                    "realized_slippage": total_slippage,
                    "pnl_gross": pnl.gross,
                    "pnl_net": pnl.net,
                    "mae": mae,
                    "mfe": mfe,
                    "closed_at": closed_at.isoformat(),
                    "close_reason": signal.reason,
                    "trade_duration_seconds": duration_seconds(
                        str(trade["opened_at"]),
                        closed_at,
                    ),
                },
            )
            self.repo.insert(
                "trade_events",
                {
                    "experiment_id": experiment["id"],
                    "trade_id": trade["id"],
                    "event_type": "PAPER_CLOSE",
                    "event_time": closed_at.isoformat(),
                    "payload": {
                        "reason": signal.reason,
                        "fill": asdict(fill),
                        "pnl": asdict(pnl),
                    },
                },
            )

            if version.startswith("V6"):
                try:
                    if not open_payload:
                        open_events = [
                            event
                            for event in self.repo.trade_events_for_trade(trade["id"])
                            if event.get("event_type") == "PAPER_OPEN"
                        ]
                        open_payload = (
                            (open_events[0].get("payload") or {}) if open_events else {}
                        )
                    adaptive = open_payload.get("v6_adaptive") or {}
                    if adaptive:
                        notional = max(float(trade.get("notional_value") or 0), 1e-12)
                        net_return = float(pnl.net) / notional
                        max_drawdown = min(
                            1.0,
                            abs(min(float(mae), 0.0)) / notional,
                        )
                        reward = reward_value(
                            net_return=net_return,
                            max_drawdown=max_drawdown,
                            drawdown_penalty=self.settings.v6_drawdown_penalty,
                        )
                        portfolio_action = adaptive.get("portfolio") or {}
                        dynamic_action = adaptive.get("dynamic_risk") or {}
                        policy_version = str(
                            portfolio_action.get("policy_version")
                            or dynamic_action.get("policy_version")
                            or "v6-shadow"
                        )
                        reward_payload = {
                            "experiment_id": experiment["id"],
                            "trade_id": trade["id"],
                            "symbol": display_symbol,
                            "policy_version": policy_version,
                            "state_json": {
                                **(open_payload.get("v6_adaptive_context") or {}),
                                "account_state_at_entry": open_payload.get(
                                    "account_state_at_entry"
                                )
                                or {},
                                "close_reason": signal.reason,
                                "trade_duration_seconds": duration_seconds(
                                    str(trade["opened_at"]),
                                    closed_at,
                                ),
                                "mae": mae,
                                "mfe": mfe,
                            },
                            "action_json": adaptive,
                            "net_return": net_return,
                            "max_drawdown": max_drawdown,
                            "reward": reward,
                            "terminal": True,
                        }
                        checksum_raw = json.dumps(
                            reward_payload,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        ).encode()
                        reward_payload["checksum"] = hashlib.sha256(
                            checksum_raw
                        ).hexdigest()
                        self.repo.insert("marl_reward_memory", reward_payload)
                except (PersistenceFailure, KeyError, TypeError, ValueError):
                    # A reward-write failure must never block closing a paper position.
                    self.repo.insert(
                        "system_events",
                        {
                            "experiment_id": experiment["id"],
                            "severity": "warning",
                            "component": "v6_reward_memory",
                            "event_type": "REWARD_WRITE_FAILED",
                            "message": "V6 reward memory write failed after paper close",
                            "payload": {"trade_id": trade["id"]},
                        },
                    )
            results.append(
                {
                    "trade_id": trade["id"],
                    "status": updated["status"],
                    "action": "CLOSED",
                    "reason": signal.reason,
                    "pnl_net": pnl.net,
                }
            )

        return {
            "version": version,
            "checked": len(open_trades),
            "closed": sum(1 for x in results if x.get("action") == "CLOSED"),
            "results": results,
        }
