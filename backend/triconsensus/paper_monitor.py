from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .config import Settings
from .db import SupabaseRepository
from .execution import PaperExecutionEngine
from .market_data import BybitPublicClient, MarketDataFailure


@dataclass(frozen=True)
class ExitSignal:
    triggered: bool
    reason: str | None = None


def evaluate_exit(trade: dict, last_price: float) -> ExitSignal:
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


class PaperTradeMonitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo = SupabaseRepository(settings)
        self.market = BybitPublicClient(settings)
        self.execution = PaperExecutionEngine()

    async def run(self, *, version: str = "V1") -> dict:
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
            signal = evaluate_exit(trade, last_price)

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

            slippage_bps = float(
                trade.get("realized_slippage_bps")
                or self.settings.max_slippage_bps / 5
            )
            # Use the configured conservative default (2 bps in normal V1 flow)
            # but never silently exceed the risk-engine ceiling.
            slippage_bps = min(slippage_bps, self.settings.max_slippage_bps)
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
