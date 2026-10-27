from __future__ import annotations

from dataclasses import asdict

from .agents import AgentService
from .consensus import full_consensus
from .domain import ConsensusOutcome
from .levels import TradeLevels, validate_proposal_levels
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
        allowed_levels: TradeLevels,
    ) -> dict:
        base_context = {
            "symbol": symbol,
            "market": market_context,
            "features": quant_features,
            "ml": ml_evidence,
            "allowed_trade_levels": asdict(allowed_levels),
            "numeric_rule": (
                "If proposing LONG or SHORT, use only the exact allowed trade levels. "
                "Do not calculate or invent alternate prices."
            ),
        }

        try:
            market = await self.agents.market_intelligence(base_context)
            proposal = await self.agents.strategist(
                {**base_context, "market_agent": market.model_dump()}
            )
        except LLMFailure as exc:
            return {
                "outcome": "NO_TRADE",
                "reason": f"llm_failure:{type(exc).__name__}",
                "risk": None,
            }

        if not validate_proposal_levels(proposal, allowed_levels):
            return {
                "outcome": "NO_TRADE",
                "reason": "strategist_used_unapproved_numeric_levels",
                "agents": {
                    "market": market.model_dump(),
                    "proposal": proposal.model_dump(),
                },
                "risk": None,
            }

        try:
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
                "reason": f"risk_agent_failure:{type(exc).__name__}",
                "agents": {
                    "market": market.model_dump(),
                    "proposal": proposal.model_dump(),
                },
                "risk": None,
            }

        consensus = full_consensus(market, proposal, critic)
        agents = {
            "market": market.model_dump(),
            "proposal": proposal.model_dump(),
            "critic": critic.model_dump(),
        }

        if consensus.outcome == ConsensusOutcome.NO_TRADE:
            return {
                "outcome": "NO_TRADE",
                "reason": consensus.reason,
                "agents": agents,
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
            "agents": agents,
            "risk": asdict(decision),
        }
