from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import os
from typing import Any

import httpx

from .config import Settings
from .execution import PaperExecutionEngine
from .market_data import BybitPublicClient, MarketDataFailure
from .paper_monitor import evaluate_exit, update_excursions
from .research_cycle import run_live_research_cycle


class PaperRuntimeGatewayFailure(RuntimeError):
    pass


class PaperRuntimeGateway:
    def __init__(self) -> None:
        self.url = (os.getenv("TRICONSENSUS_SUPABASE_URL") or "").rstrip("/")
        self.publishable_key = os.getenv("TRICONSENSUS_SUPABASE_PUBLISHABLE_KEY") or ""
        self.runtime_token = os.getenv("TRICONSENSUS_DASHBOARD_TOKEN") or ""
        if not self.url or not self.publishable_key or not self.runtime_token:
            raise PaperRuntimeGatewayFailure("paper runtime Supabase credentials are missing")

    def _headers(self) -> dict[str, str]:
        headers = {
            "apikey": self.publishable_key,
            "Content-Type": "application/json",
        }
        # Legacy anon keys are JWTs and may also be used as the bearer token.
        # Modern sb_publishable_* keys must stay on the apikey header only.
        if self.publishable_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self.publishable_key}"
        return headers

    async def _rpc(self, name: str, payload: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"{self.url}/rest/v1/rpc/{name}",
                headers=self._headers(),
                json=payload,
            )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise PaperRuntimeGatewayFailure(
                f"{name} failed with HTTP {response.status_code}: {detail}"
            )
        return response.json()

    async def dashboard_snapshot(self) -> dict:
        result = await self._rpc(
            "triconsensus_dashboard_snapshot",
            {"p_token": self.runtime_token},
        )
        if not isinstance(result, dict):
            raise PaperRuntimeGatewayFailure("dashboard snapshot returned invalid payload")
        return result

    async def open_trade(self, payload: dict) -> dict:
        result = await self._rpc(
            "triconsensus_open_paper_trade",
            {"p_token": self.runtime_token, "p_payload": payload},
        )
        if not isinstance(result, dict):
            raise PaperRuntimeGatewayFailure("open RPC returned invalid payload")
        return result

    async def monitor_trade(self, payload: dict) -> dict:
        result = await self._rpc(
            "triconsensus_monitor_paper_trade",
            {"p_token": self.runtime_token, "p_payload": payload},
        )
        if not isinstance(result, dict):
            raise PaperRuntimeGatewayFailure("monitor RPC returned invalid payload")
        return result

    async def close_trade(self, payload: dict) -> dict:
        result = await self._rpc(
            "triconsensus_close_paper_trade",
            {"p_token": self.runtime_token, "p_payload": payload},
        )
        if not isinstance(result, dict):
            raise PaperRuntimeGatewayFailure("close RPC returned invalid payload")
        return result


def _assert_shadow_only(agent_result: dict) -> None:
    adaptive = agent_result.get("v6_adaptive") or {}
    for role in ("portfolio", "dynamic_risk"):
        action = adaptive.get(role)
        if action is not None and not bool(action.get("shadow_mode", True)):
            raise PaperRuntimeGatewayFailure(
                f"V6 {role} attempted enforcement in paper runtime"
            )


class PaperRuntimeService:
    def __init__(self, settings: Settings) -> None:
        if settings.trading_mode != "paper":
            raise PaperRuntimeGatewayFailure("paper runtime refuses non-paper mode")
        if settings.v6_marl_enforcement_active:
            raise PaperRuntimeGatewayFailure("paper runtime refuses V6 enforcement")
        self.settings = settings
        self.gateway = PaperRuntimeGateway()
        self.execution = PaperExecutionEngine()
        self.market = BybitPublicClient(settings)

    async def run_symbol(
        self,
        *,
        symbol: str,
        vercel_oidc_token: str | None,
    ) -> dict:
        snapshot = await self.gateway.dashboard_snapshot()
        ledger = snapshot.get("paper_trades") or []
        result = await run_live_research_cycle(
            self.settings,
            symbol=symbol,
            vercel_oidc_token=vercel_oidc_token,
            ledger=ledger,
            include_persistence_context=True,
        )

        if result.get("outcome") not in {"LONG", "SHORT"}:
            result.pop("persistence_context", None)
            return {
                **result,
                "paper_runtime": "NO_TRADE",
                "persisted": False,
                "execution_performed": False,
            }

        gate = result.get("protocol_gate") or {}
        agent_result = result.get("agent_result") or {}
        risk = agent_result.get("risk") or {}
        proposal = (agent_result.get("agents") or {}).get("proposal") or {}
        if not (
            bool(gate.get("research_paper_candidate"))
            and bool(gate.get("v51_direction_match"))
            and bool(gate.get("deterministic_risk_approved"))
            and bool(agent_result.get("full_consensus"))
            and bool(risk.get("approved"))
        ):
            result.pop("persistence_context", None)
            return {
                **result,
                "outcome": "NO_TRADE",
                "reason": "paper_runtime_gate_rejected",
                "paper_runtime": "NO_TRADE",
                "persisted": False,
                "execution_performed": False,
            }

        _assert_shadow_only(agent_result)

        entry_zone = proposal.get("entry_zone") or []
        if not entry_zone:
            raise PaperRuntimeGatewayFailure("approved proposal has no entry zone")
        requested_entry = sum(float(x) for x in entry_zone) / len(entry_zone)
        quantity = float(risk.get("position_size") or 0)
        context = result.get("persistence_context") or {}
        account_state = context.get("account_state") or {}
        if quantity <= 0:
            raise PaperRuntimeGatewayFailure("approved risk decision has no position size")

        fill = self.execution.open_market(
            side=str(result["outcome"]),
            requested_price=requested_entry,
            quantity=quantity,
            slippage_bps=float(account_state.get("slippage_bps") or 0),
        )

        # The deterministic risk object intentionally does not carry the proposal
        # entry. Add it only to the immutable audit payload consumed by Supabase.
        audit_agent_result = {
            **agent_result,
            "risk": {**risk, "entry": requested_entry},
        }
        payload = {
            "paper_only": True,
            "live_trading": False,
            "v6_enforcement": False,
            "symbol": result["symbol"],
            "side": result["outcome"],
            "observed_at": result["observed_at"],
            "strategy": result["v51_signal"],
            "provider": result["provider"],
            "model_id": result["model_id"],
            "agent_result": audit_agent_result,
            "protocol_gate": gate,
            "paper_fill": asdict(fill),
            "risk_fraction": self.settings.risk_per_trade,
            "market_snapshot": context.get("market_snapshot") or {},
            "feature_snapshot": context.get("feature_snapshot") or {},
            "account_state": account_state,
            "v6_adaptive_context": context.get("v6_adaptive_context") or {},
        }
        persisted = await self.gateway.open_trade(payload)
        result.pop("persistence_context", None)
        return {
            **result,
            "paper_runtime": persisted.get("status", "UNKNOWN"),
            "persistence": persisted,
            "persisted": persisted.get("status") in {"OPENED", "EXISTING"},
            "execution_performed": False,
            "simulated_fill": asdict(fill),
        }

    async def monitor(self) -> dict:
        snapshot = await self.gateway.dashboard_snapshot()
        trades = [
            trade
            for trade in (snapshot.get("paper_trades") or [])
            if trade.get("status") == "open"
        ]
        results: list[dict] = []
        ticker_cache: dict[str, dict] = {}

        for trade in reversed(trades):
            symbol = str(trade["symbol"]).replace("/", "")
            try:
                if symbol not in ticker_cache:
                    ticker_cache[symbol] = await self.market.ticker(symbol)
                ticker = ticker_cache[symbol]
            except (MarketDataFailure, ValueError) as exc:
                results.append(
                    {
                        "trade_id": trade["id"],
                        "action": "NO_ACTION",
                        "reason": "market_data_failure",
                        "error": type(exc).__name__,
                    }
                )
                continue

            last_price = float(ticker["last_price"])
            mae, mfe = update_excursions(trade, last_price)
            signal = evaluate_exit(trade, last_price, mfe=mfe, trailing_lock_fraction=0.0)

            if not signal.triggered:
                updated = await self.gateway.monitor_trade(
                    {
                        "paper_only": True,
                        "live_trading": False,
                        "v6_enforcement": False,
                        "trade_id": trade["id"],
                        "mae": mae,
                        "mfe": mfe,
                    }
                )
                results.append(
                    {
                        "trade_id": trade["id"],
                        "action": "MONITORED",
                        "last_price": last_price,
                        "persistence": updated,
                    }
                )
                continue

            if signal.reason == "INVALID_SIDE":
                results.append(
                    {
                        "trade_id": trade["id"],
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
            closed_at = datetime.now(UTC)
            persisted = await self.gateway.close_trade(
                {
                    "paper_only": True,
                    "live_trading": False,
                    "v6_enforcement": False,
                    "trade_id": trade["id"],
                    "requested_exit": last_price,
                    "actual_exit": fill.fill_price,
                    "close_fee": fill.fee,
                    "close_slippage": fill.slippage_cost,
                    "mae": mae,
                    "mfe": mfe,
                    "closed_at": closed_at.isoformat(),
                    "close_reason": signal.reason,
                }
            )
            results.append(
                {
                    "trade_id": trade["id"],
                    "action": "CLOSED",
                    "reason": signal.reason,
                    "persistence": persisted,
                }
            )

        return {
            "paper_only": True,
            "live_trading": False,
            "v6_enforcement": False,
            "checked": len(trades),
            "closed": sum(1 for row in results if row.get("action") == "CLOSED"),
            "results": results,
        }
