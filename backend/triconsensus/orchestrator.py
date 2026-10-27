from __future__ import annotations

from dataclasses import asdict

from .agents import AgentService
from .consensus import full_consensus
from .domain import ConsensusOutcome
from .llm import LLMFailure
from .risk import DeterministicRiskEngine, RiskInput


class DecisionOrchestrator:
    def __init__(self, agents: AgentService, risk: DeterministicRiskEngine) -> None:
        self.agents = agents
        self.risk = risk

    async def decide(
        self,
        *,
        symbol: str,
        market_context: dict,
        quant_features: dict,
        ml_evidence: dict,
        account_state: dict,
    ) -> dict:
        base_context = {
            "symbol": symbol,
            "market": market_context,
            "features": quant_features,
            "ml": ml_evidence,
        }

        try:
            market = await self.agents.market_intelligence(base_context)
            proposal = await self.agents.strategist(
                {**base_context, "market_agent": market.model_dump()}
            )
            critic = await self.agents.risk_advocate(
                {
                    **base_context,
                    "market_agent": market.model_dump(),
                    "trade_proposal": proposal.model_dump(),
                }
            )
        except LLMFailure as exc:
            return {
                "outcome": "NO_TRADE",
                "reason": f"llm_failure:{type(exc).__name__}",
                "risk": None,
            }

        consensus = full_consensus(market, proposal, critic)
        if consensus.outcome == ConsensusOutcome.NO_TRADE:
            return {
                "outcome": "NO_TRADE",
                "reason": consensus.reason,
                "agents": {
                    "market": market.model_dump(),
                    "proposal": proposal.model_dump(),
                    "critic": critic.model_dump(),
                },
                "risk": None,
            }

        if not proposal.entry_zone or proposal.stop_loss is None or proposal.take_profit_1 is None:
            return {
                "outcome": "NO_TRADE",
                "reason": "missing_numeric_trade_levels",
                "risk": None,
            }

        entry = sum(proposal.entry_zone) / len(proposal.entry_zone)
        decision = self.risk.evaluate(
            RiskInput(
                equity=float(account_state["equity"]),
                daily_pnl=float(account_state.get("daily_pnl", 0)),
                peak_equity=float(account_state["peak_equity"]),
                open_positions=int(account_state.get("open_positions", 0)),
                entry=entry,
                stop_loss=float(proposal.stop_loss),
                take_profit=float(proposal.take_profit_1),
                spread_bps=float(quant_features.get("spread_bps") or 0),
                slippage_bps=float(account_state.get("slippage_bps", 2)),
                market_data_age_ms=int(market_context["market_data_age_ms"]),
                side=consensus.outcome.value,
            )
        )

        return {
            "outcome": consensus.outcome.value if decision.approved else "NO_TRADE",
            "reason": decision.reason,
            "agents": {
                "market": market.model_dump(),
                "proposal": proposal.model_dump(),
                "critic": critic.model_dump(),
            },
            "risk": asdict(decision),
        }
