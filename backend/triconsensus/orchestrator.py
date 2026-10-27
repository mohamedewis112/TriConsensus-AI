from __future__ import annotations

from .agents import AgentService
from .graph import TriConsensusGraph
from .levels import TradeLevels
from .risk import DeterministicRiskEngine


class DecisionOrchestrator:
    """Thin compatibility wrapper around the LangGraph decision graph."""

    def __init__(self, agents: AgentService, risk: DeterministicRiskEngine) -> None:
        self.graph = TriConsensusGraph(agents, risk)

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
        return await self.graph.decide(
            symbol=symbol,
            market_context=market_context,
            quant_features=quant_features,
            ml_evidence=ml_evidence,
            account_state=account_state,
            allowed_levels=allowed_levels,
        )
