from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from .config import Settings
from .execution import PaperExecutionEngine
from .market_data import BybitPublicClient, MarketDataFailure
from .paper_monitor import duration_seconds, evaluate_exit, update_excursions
from .research_cycle import run_live_research_cycle
from .runtime_gateway import PaperRuntimeGateway, RuntimeGatewayFailure
from .v6_models import RewardMemoryRecord


class PaperRuntimeFailure(RuntimeError):
    pass


def _reward_records(rows: list[dict]) -> list[RewardMemoryRecord]:
    records: list[RewardMemoryRecord] = []
    for row in rows:
        try:
            records.append(
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
        except (KeyError, TypeError, ValueError):
            continue
    return records


def _assert_shadow_only(agent_result: dict) -> None:
    adaptive = agent_result.get("v6_adaptive") or {}
    for role in ("portfolio", "dynamic_risk"):
        action = adaptive.get(role)
        if action is not None and not bool(action.get("shadow_mode", True)):
            raise PaperRuntimeFailure(
                f"V6 {role} attempted enforcement in paper runtime"
            )


class PaperRuntimeService:
    def __init__(self, settings: Settings, *, vercel_oidc_token: str | None) -> None:
        if settings.trading_mode.value != "paper":
            raise PaperRuntimeFailure("paper runtime refuses non-paper mode")
        if settings.v6_marl_enforcement_active:
            raise PaperRuntimeFailure("paper runtime refuses V6 enforcement")
        self.settings = settings
        self.gateway = PaperRuntimeGateway(
            settings,
            vercel_oidc_token=vercel_oidc_token,
        )
        self.execution = PaperExecutionEngine()
        self.market = BybitPublicClient(settings)
        self.oidc_token = vercel_oidc_token

    async def status(self) -> dict:
        snapshot = await self.gateway.snapshot()
        trades = list(snapshot.get("paper_trades") or [])
        rewards = list(snapshot.get("reward_memory") or [])
        return {
            "status": "ok",
            "paper_only": True,
            "live_trading": False,
            "v6_enforcement": False,
            "experiment": snapshot.get("experiment") or {},
            "paper_trades": {
                "total": len(trades),
                "open": sum(1 for row in trades if row.get("status") == "open"),
                "closed": sum(1 for row in trades if row.get("status") == "closed"),
            },
            "reward_memory_count": len(rewards),
        }

    async def run_symbol(self, *, symbol: str) -> dict:
        snapshot = await self.gateway.snapshot()
        ledger = list(snapshot.get("paper_trades") or [])
        rewards = _reward_records(list(snapshot.get("reward_memory") or []))

        result = await run_live_research_cycle(
            self.settings,
            symbol=symbol,
            vercel_oidc_token=self.oidc_token,
            ledger=ledger,
            reward_memory=rewards,
            include_persistence_context=True,
        )

        context = result.pop("persistence_context", None)
        if context is None:
            return {
                **result,
                "paper_runtime": "NO_TRADE",
                "runtime_commit": None,
                "paper_trade_opened": False,
            }

        agent_result = result.get("agent_result") or {}
        gate = result.get("protocol_gate") or {}
        account_state = context.get("account_state") or {}
        _assert_shadow_only(agent_result)

        paper_fill = None
        opened_at = None
        if bool(gate.get("research_paper_candidate")):
            proposal = (agent_result.get("agents") or {}).get("proposal") or {}
            risk = agent_result.get("risk") or {}
            entry_zone = [float(value) for value in (proposal.get("entry_zone") or [])]
            if not entry_zone:
                raise PaperRuntimeFailure("qualified candidate has no entry zone")
            quantity = float(risk.get("position_size") or 0)
            if quantity <= 0:
                raise PaperRuntimeFailure(
                    "qualified candidate has no deterministic position size"
                )
            requested_entry = sum(entry_zone) / len(entry_zone)
            fill = self.execution.open_market(
                side=str(result["outcome"]),
                requested_price=requested_entry,
                quantity=quantity,
                slippage_bps=float(account_state.get("slippage_bps") or 0),
            )
            paper_fill = asdict(fill)
            opened_at = datetime.now(UTC).isoformat()

        payload = {
            "paper_only": True,
            "live_trading": False,
            "v6_enforcement": False,
            "symbol": result["symbol"],
            "v51_signal": result["v51_signal"],
            "observed_at": result["observed_at"],
            "market_snapshot": context["market_snapshot"],
            "feature_snapshot": context["feature_snapshot"],
            "account_state": account_state,
            "v6_adaptive_context": context.get("v6_adaptive_context") or {},
            "agent_result": agent_result,
            "protocol_gate": gate,
            "llm_provider": result.get("provider") or self.settings.llm_provider,
            "llm_model": result.get("model_id")
            or self.settings.vercel_ai_gateway_model,
        }
        if paper_fill is not None:
            payload["paper_fill"] = paper_fill
            payload["opened_at"] = opened_at

        committed = await self.gateway.commit(payload)
        return {
            **result,
            "persisted": bool(committed.get("committed")),
            "paper_runtime": (
                "OPENED"
                if committed.get("opened")
                else "EXISTING"
                if committed.get("duplicate")
                else "NO_TRADE"
            ),
            "paper_trade_opened": bool(committed.get("opened")),
            "duplicate": bool(committed.get("duplicate")),
            "runtime_commit": committed,
            "simulated_fill": paper_fill,
        }

    async def monitor(self) -> dict:
        snapshot = await self.gateway.snapshot()
        open_trades = [
            row
            for row in (snapshot.get("paper_trades") or [])
            if row.get("status") == "open"
        ]
        ticker_cache: dict[str, dict] = {}
        results: list[dict] = []

        for trade in open_trades:
            display_symbol = str(trade["symbol"])
            bybit_symbol = display_symbol.replace("/", "")
            try:
                if bybit_symbol not in ticker_cache:
                    ticker_cache[bybit_symbol] = await self.market.ticker(bybit_symbol)
                last_price = float(ticker_cache[bybit_symbol]["last_price"])
            except (MarketDataFailure, ValueError, KeyError, TypeError):
                results.append(
                    {
                        "trade_id": trade["id"],
                        "action": "NO_ACTION",
                        "reason": "market_data_failure",
                    }
                )
                continue

            mae, mfe = update_excursions(trade, last_price)
            signal = evaluate_exit(
                trade,
                last_price,
                mfe=mfe,
                trailing_lock_fraction=0.0,
            )

            if not signal.triggered:
                persisted = await self.gateway.monitor(
                    {
                        "paper_only": True,
                        "live_trading": False,
                        "v6_enforcement": False,
                        "trade_id": trade["id"],
                        "action": "MONITOR",
                        "mae": mae,
                        "mfe": mfe,
                    }
                )
                results.append(
                    {
                        "trade_id": trade["id"],
                        "action": "MONITORED",
                        "last_price": last_price,
                        "persistence": persisted,
                    }
                )
                continue

            if signal.reason not in {"STOP_LOSS", "TAKE_PROFIT_1"}:
                results.append(
                    {
                        "trade_id": trade["id"],
                        "action": "NO_ACTION",
                        "reason": signal.reason or "invalid_exit_signal",
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
            total_slippage = (
                float(trade.get("realized_slippage") or 0) + fill.slippage_cost
            )
            pnl = self.execution.pnl(
                position_side=str(trade["side"]),
                entry_fill=float(trade["actual_entry"]),
                exit_fill=fill.fill_price,
                quantity=float(trade["position_size"]),
                total_fees=total_fees,
            )
            closed_at = datetime.now(UTC)

            persisted = await self.gateway.monitor(
                {
                    "paper_only": True,
                    "live_trading": False,
                    "v6_enforcement": False,
                    "trade_id": trade["id"],
                    "action": "CLOSE",
                    "close_reason": signal.reason,
                    "requested_exit": last_price,
                    "actual_exit": fill.fill_price,
                    "realized_fees": total_fees,
                    "realized_slippage": total_slippage,
                    "pnl_gross": pnl.gross,
                    "pnl_net": pnl.net,
                    "mae": mae,
                    "mfe": mfe,
                    "closed_at": closed_at.isoformat(),
                    "trade_duration_seconds": duration_seconds(
                        str(trade["opened_at"]),
                        closed_at,
                    ),
                    "fill": asdict(fill),
                }
            )
            results.append(
                {
                    "trade_id": trade["id"],
                    "action": "CLOSED",
                    "reason": signal.reason,
                    "pnl_net": pnl.net,
                    "persistence": persisted,
                }
            )

        return {
            "paper_only": True,
            "live_trading": False,
            "v6_enforcement": False,
            "checked": len(open_trades),
            "closed": sum(
                1 for row in results if row.get("action") == "CLOSED"
            ),
            "results": results,
        }
