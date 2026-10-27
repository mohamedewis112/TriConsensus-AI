from __future__ import annotations

from dataclasses import dataclass

from .domain import TradeDecision, TradeProposal


@dataclass(frozen=True)
class TradeLevels:
    long: dict[str, float]
    short: dict[str, float]


def build_trade_levels(
    *,
    last_price: float,
    atr: float,
    min_risk_reward: float,
    stop_atr: float = 1.5,
) -> TradeLevels:
    if last_price <= 0 or atr <= 0:
        raise ValueError("invalid market levels")
    if min_risk_reward < 1:
        raise ValueError("invalid minimum risk/reward")

    stop_distance = atr * stop_atr
    target_distance = stop_distance * min_risk_reward

    return TradeLevels(
        long={
            "entry": last_price,
            "stop_loss": last_price - stop_distance,
            "take_profit_1": last_price + target_distance,
            "take_profit_2": last_price + (target_distance * 1.5),
        },
        short={
            "entry": last_price,
            "stop_loss": last_price + stop_distance,
            "take_profit_1": last_price - target_distance,
            "take_profit_2": last_price - (target_distance * 1.5),
        },
    )


def validate_proposal_levels(
    proposal: TradeProposal,
    allowed: TradeLevels,
    *,
    tolerance_bps: float = 1.0,
) -> bool:
    if proposal.decision == TradeDecision.WAIT:
        return True
    if not proposal.entry_zone or proposal.stop_loss is None or proposal.take_profit_1 is None:
        return False

    target = allowed.long if proposal.decision == TradeDecision.LONG else allowed.short
    entry = sum(proposal.entry_zone) / len(proposal.entry_zone)

    def close_enough(actual: float, expected: float) -> bool:
        tolerance = expected * tolerance_bps / 10_000.0
        return abs(actual - expected) <= tolerance

    if not close_enough(entry, target["entry"]):
        return False
    if not close_enough(float(proposal.stop_loss), target["stop_loss"]):
        return False
    if not close_enough(float(proposal.take_profit_1), target["take_profit_1"]):
        return False
    if proposal.take_profit_2 is not None and not close_enough(
        float(proposal.take_profit_2), target["take_profit_2"]
    ):
        return False
    return True
