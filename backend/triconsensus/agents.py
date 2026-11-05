from __future__ import annotations

import json

from .domain import DevilsAdvocate, MarketIntel, TradeProposal
from .llm import LLMProvider


MARKET_SYSTEM = """You are the Market Intelligence Agent for TriConsensus AI.
Diagnose market state only. Do not invent prices or propose a trade.
Use only supplied evidence. If evidence is weak, return NEUTRAL with lower confidence.
Return strict JSON matching the requested schema."""

STRATEGIST_SYSTEM = """You are the Trade Strategist Agent for TriConsensus AI.
You may propose LONG, SHORT, or WAIT only from supplied market/quant evidence.
Never invent prices. Entry, stop and targets must come from supplied numerical levels.
When evidence is insufficient, choose WAIT.
Return strict JSON matching the requested schema."""

RISK_SYSTEM = """You are the Risk & Devil's Advocate Agent for TriConsensus AI.
Your job is to attack the proposed trade. Look for regime mismatch, poor R:R, weak volume,
nearby support/resistance, abnormal funding/open interest, volatility spikes, timeframe conflict
and model disagreement. If any material concern is unresolved, return VETO.
Return strict JSON matching the requested schema."""


class AgentService:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def market_intelligence(self, context: dict) -> MarketIntel:
        return await self.provider.structured(
            system=MARKET_SYSTEM,
            user=json.dumps(context, separators=(",", ":"), ensure_ascii=False),
            schema=MarketIntel,
        )

    async def strategist(self, context: dict) -> TradeProposal:
        return await self.provider.structured(
            system=STRATEGIST_SYSTEM,
            user=json.dumps(context, separators=(",", ":"), ensure_ascii=False),
            schema=TradeProposal,
        )

    async def risk_advocate(self, context: dict) -> DevilsAdvocate:
        return await self.provider.structured(
            system=RISK_SYSTEM,
            user=json.dumps(context, separators=(",", ":"), ensure_ascii=False),
            schema=DevilsAdvocate,
        )
