from triconsensus.domain import TradeDecision, TradeProposal
from triconsensus.levels import build_trade_levels, validate_proposal_levels


def test_deterministic_levels_validate_exact_proposal():
    levels = build_trade_levels(last_price=100, atr=2, min_risk_reward=2)
    p = TradeProposal(
        decision=TradeDecision.LONG,
        symbol="BTC/USDT",
        entry_zone=[100],
        stop_loss=97,
        take_profit_1=106,
        take_profit_2=109,
        risk_reward=2,
        confidence=0.8,
    )
    assert validate_proposal_levels(p, levels) is True


def test_invented_price_is_rejected():
    levels = build_trade_levels(last_price=100, atr=2, min_risk_reward=2)
    p = TradeProposal(
        decision=TradeDecision.LONG,
        symbol="BTC/USDT",
        entry_zone=[105],
        stop_loss=97,
        take_profit_1=106,
        confidence=0.8,
    )
    assert validate_proposal_levels(p, levels) is False
