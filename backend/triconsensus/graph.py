from __future__ import annotations

from dataclasses import asdict
from typing import Any
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph

from .agents import AgentService
from .consensus import full_consensus
from .domain import ConsensusOutcome, DevilsAdvocate, MarketIntel, TradeProposal
from .levels import TradeLevels, validate_proposal_levels
from .llm import LLMFailure
from .risk import DeterministicRiskEngine, RiskInput


class DecisionState(TypedDict, total=False):
    symbol: str
    market_context: dict[str, Any]
    quant_features: dict[str, Any]
    ml_evidence: dict[str, Any]
    account_state: dict[str, Any]
    allowed_levels: TradeLevels
    market_agent: MarketIntel
    proposal: TradeProposal
    critic: DevilsAdvocate
    failure_reason: str
    consensus_outcome: str
    consensus_reason: str
    result: dict[str, Any]


class TriConsensusGraph:
    def __init__(self, agents: AgentService, risk: DeterministicRiskEngine) -> None:
        self.agents = agents
        self.risk = risk
        self.compiled = self._build()

    def _base_context(self, state: DecisionState) -> dict[str, Any]:
        return {
            "symbol": state["symbol"],
            "market": state["market_context"],
            "features": state["quant_features"],
            "ml": state["ml_evidence"],
            "allowed_trade_levels": asdict(state["allowed_levels"]),
            "numeric_rule": (
                "If proposing LONG or SHORT, use only the exact allowed trade levels. "
                "Do not calculate or invent alternate prices."
            ),
        }

    async def market_node(self, state: DecisionState) -> dict[str, Any]:
        try:
            result = await self.agents.market_intelligence(self._base_context(state))
            return {"market_agent": result}
        except LLMFailure as exc:
            return {"failure_reason": f"market_agent_failure:{type(exc).__name__}"}

    def after_market(self, state: DecisionState) -> str:
        return "fail" if state.get("failure_reason") else "strategist"

    async def strategist_node(self, state: DecisionState) -> dict[str, Any]:
        try:
            context = {
                **self._base_context(state),
                "market_agent": state["market_agent"].model_dump(),
            }
            result = await self.agents.strategist(context)
            if not validate_proposal_levels(result, state["allowed_levels"]):
                return {
                    "proposal": result,
                    "failure_reason": "strategist_used_unapproved_numeric_levels",
                }
            return {"proposal": result}
        except LLMFailure as exc:
            return {"failure_reason": f"strategist_failure:{type(exc).__name__}"}

    def after_strategist(self, state: DecisionState) -> str:
        return "fail" if state.get("failure_reason") else "critic"

    async def critic_node(self, state: DecisionState) -> dict[str, Any]:
        try:
            context = {
                **self._base_context(state),
                "market_agent": state["market_agent"].model_dump(),
                "trade_proposal": state["proposal"].model_dump(),
            }
            result = await self.agents.risk_advocate(context)
            return {"critic": result}
        except LLMFailure as exc:
            return {"failure_reason": f"risk_agent_failure:{type(exc).__name__}"}

    def after_critic(self, state: DecisionState) -> str:
        return "fail" if state.get("failure_reason") else "consensus"

    def consensus_node(self, state: DecisionState) -> dict[str, Any]:
        consensus = full_consensus(
            state["market_agent"],
            state["proposal"],
            state["critic"],
        )
        return {
            "consensus_outcome": consensus.outcome.value,
            "consensus_reason": consensus.reason,
        }

    def after_consensus(self, state: DecisionState) -> str:
        return (
            "risk"
            if state.get("consensus_outcome") in {
                ConsensusOutcome.LONG.value,
                ConsensusOutcome.SHORT.value,
            }
            else "no_trade"
        )

    def risk_node(self, state: DecisionState) -> dict[str, Any]:
        proposal = state["proposal"]
        account = state["account_state"]
        features = state["quant_features"]
        market = state["market_context"]
        entry = sum(proposal.entry_zone) / len(proposal.entry_zone)

        decision = self.risk.evaluate(
            RiskInput(
                equity=float(account["equity"]),
                day_start_equity=float(account.get("day_start_equity") or account["equity"]),
                daily_pnl=float(account.get("daily_pnl", 0)),
                peak_equity=float(account["peak_equity"]),
                open_positions=int(account.get("open_positions", 0)),
                current_exposure=float(account.get("current_exposure", 0)),
                correlated_exposure=float(account.get("correlated_exposure", 0)),
                losing_streak=int(account.get("losing_streak", 0)),
                minutes_since_last_loss=int(account.get("minutes_since_last_loss", 999999)),
                kill_switch_active=bool(account.get("kill_switch_active", False)),
                entry=entry,
                stop_loss=float(proposal.stop_loss),
                take_profit=float(proposal.take_profit_1),
                spread_bps=float(features.get("spread_bps") or 0),
                slippage_bps=float(account.get("slippage_bps", 2)),
                realized_volatility_20=float(features.get("realized_vol_20") or 0),
                market_data_age_ms=int(market["market_data_age_ms"]),
                side=state["consensus_outcome"],
            )
        )
        agents = self._agent_payload(state)
        return {
            "result": {
                "outcome": state["consensus_outcome"] if decision.approved else "NO_TRADE",
                "reason": decision.reason,
                "agents": agents,
                "risk": asdict(decision),
            }
        }

    def no_trade_node(self, state: DecisionState) -> dict[str, Any]:
        return {
            "result": {
                "outcome": "NO_TRADE",
                "reason": state.get("consensus_reason", "consensus_rejected"),
                "agents": self._agent_payload(state),
                "risk": None,
            }
        }

    def fail_node(self, state: DecisionState) -> dict[str, Any]:
        return {
            "result": {
                "outcome": "NO_TRADE",
                "reason": state.get("failure_reason", "unknown_graph_failure"),
                "agents": self._agent_payload(state),
                "risk": None,
            }
        }

    @staticmethod
    def _agent_payload(state: DecisionState) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if state.get("market_agent") is not None:
            payload["market"] = state["market_agent"].model_dump()
        if state.get("proposal") is not None:
            payload["proposal"] = state["proposal"].model_dump()
        if state.get("critic") is not None:
            payload["critic"] = state["critic"].model_dump()
        return payload

    def _build(self):
        builder = StateGraph(DecisionState)
        builder.add_node("market", self.market_node)
        builder.add_node("strategist", self.strategist_node)
        builder.add_node("critic", self.critic_node)
        builder.add_node("consensus", self.consensus_node)
        builder.add_node("risk", self.risk_node)
        builder.add_node("no_trade", self.no_trade_node)
        builder.add_node("fail", self.fail_node)

        builder.add_edge(START, "market")
        builder.add_conditional_edges(
            "market",
            self.after_market,
            {"strategist": "strategist", "fail": "fail"},
        )
        builder.add_conditional_edges(
            "strategist",
            self.after_strategist,
            {"critic": "critic", "fail": "fail"},
        )
        builder.add_conditional_edges(
            "critic",
            self.after_critic,
            {"consensus": "consensus", "fail": "fail"},
        )
        builder.add_conditional_edges(
            "consensus",
            self.after_consensus,
            {"risk": "risk", "no_trade": "no_trade"},
        )
        builder.add_edge("risk", END)
        builder.add_edge("no_trade", END)
        builder.add_edge("fail", END)
        return builder.compile()

    async def decide(self, **state: Any) -> dict[str, Any]:
        final = await self.compiled.ainvoke(state)
        return final["result"]
