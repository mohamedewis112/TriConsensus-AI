from __future__ import annotations

from .domain import (
    Bias,
    ConsensusOutcome,
    ConsensusResult,
    DevilsAdvocate,
    MarketIntel,
    RiskVerdict,
    TradeDecision,
    TradeProposal,
)


def full_consensus(
    market: MarketIntel,
    proposal: TradeProposal,
    critic: DevilsAdvocate,
) -> ConsensusResult:
    if critic.verdict == RiskVerdict.VETO:
        return ConsensusResult(
            outcome=ConsensusOutcome.NO_TRADE,
            full_consensus=False,
            reason="Risk agent veto",
        )

    if proposal.decision == TradeDecision.WAIT or market.bias == Bias.NEUTRAL:
        return ConsensusResult(
            outcome=ConsensusOutcome.NO_TRADE,
            full_consensus=False,
            reason="WAIT or neutral market state",
        )

    if market.bias == Bias.BULLISH and proposal.decision == TradeDecision.LONG:
        return ConsensusResult(
            outcome=ConsensusOutcome.LONG,
            full_consensus=True,
            reason="Full bullish consensus",
        )

    if market.bias == Bias.BEARISH and proposal.decision == TradeDecision.SHORT:
        return ConsensusResult(
            outcome=ConsensusOutcome.SHORT,
            full_consensus=True,
            reason="Full bearish consensus",
        )

    return ConsensusResult(
        outcome=ConsensusOutcome.NO_TRADE,
        full_consensus=False,
        reason="Agent disagreement",
    )
