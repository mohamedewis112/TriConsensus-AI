from __future__ import annotations

from .agents import AgentService
from .graph import TriConsensusGraph
from .levels import TradeLevels
from .risk import DeterministicRiskEngine
from .v6_agents import DynamicVolatilityRiskAgent, MetaRLPortfolioAdapterAgent


class DecisionOrchestrator:
    """Thin compatibility wrapper around the LangGraph decision graph."""

    def __init__(
        self,
        agents: AgentService,
        risk: DeterministicRiskEngine,
        portfolio_adapter: MetaRLPortfolioAdapterAgent | None = None,
        dynamic_risk_agent: DynamicVolatilityRiskAgent | None = None,
    ) -> None:
        self.graph = TriConsensusGraph(
            agents,
            risk,
            portfolio_adapter=portfolio_adapter,
            dynamic_risk_agent=dynamic_risk_agent,
        )

    async def decide(
        self,
        *,
        symbol: str,
        market_context: dict,
        quant_features: dict,
        ml_evidence: dict,
        account_state: dict,
        allowed_levels: TradeLevels,
        adaptive_context: dict | None = None,
    ) -> dict:
        return await self.graph.decide(
            symbol=symbol,
            market_context=market_context,
            quant_features=quant_features,
            ml_evidence=ml_evidence,
            account_state=account_state,
            allowed_levels=allowed_levels,
            adaptive_context=adaptive_context or {},
        )
