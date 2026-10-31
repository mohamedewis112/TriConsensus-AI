from triconsensus.consensus import full_consensus
from triconsensus.domain import (
    Bias, DevilsAdvocate, MarketIntel, RiskVerdict, TradeDecision, TradeProposal
)


def market(bias: Bias) -> MarketIntel:
    return MarketIntel(market_regime="trend", bias=bias, confidence=0.8)


def proposal(decision: TradeDecision) -> TradeProposal:
    return TradeProposal(decision=decision, symbol="BTC/USDT", confidence=0.8)


def critic(verdict: RiskVerdict) -> DevilsAdvocate:
    return DevilsAdvocate(verdict=verdict, confidence=0.8)


def test_veto_always_means_no_trade():
    out = full_consensus(market(Bias.BULLISH), proposal(TradeDecision.LONG), critic(RiskVerdict.VETO))
    assert out.outcome == "NO_TRADE"
    assert out.full_consensus is False


def test_full_bullish_consensus_allows_long():
    out = full_consensus(market(Bias.BULLISH), proposal(TradeDecision.LONG), critic(RiskVerdict.APPROVE))
    assert out.outcome == "LONG"
    assert out.full_consensus is True


def test_disagreement_means_no_trade():
    out = full_consensus(market(Bias.BEARISH), proposal(TradeDecision.LONG), critic(RiskVerdict.APPROVE))
    assert out.outcome == "NO_TRADE"
