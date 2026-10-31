from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, Field


class Bias(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class TradeDecision(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"


class RiskVerdict(StrEnum):
    APPROVE = "APPROVE"
    VETO = "VETO"


class ConsensusOutcome(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class MarketIntel(BaseModel):
    market_regime: str
    bias: Bias
    confidence: float = Field(ge=0, le=1)
    conditions: list[str] = []
    risks: list[str] = []
    evidence: list[str] = []


class TradeProposal(BaseModel):
    decision: TradeDecision
    symbol: str
    entry_zone: list[float] = []
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None
    risk_reward: float | None = None
    confidence: float = Field(ge=0, le=1)
    setup_type: str = ""
    invalidation: str = ""
    evidence: list[str] = []


class DevilsAdvocate(BaseModel):
    verdict: RiskVerdict
    confidence: float = Field(ge=0, le=1)
    risk_flags: list[str] = []
    reason: str = ""
    evidence: list[str] = []


class ConsensusResult(BaseModel):
    outcome: ConsensusOutcome
    full_consensus: bool
    reason: str
