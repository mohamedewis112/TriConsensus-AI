from __future__ import annotations

from dataclasses import dataclass

from .config import Settings


@dataclass(frozen=True)
class RiskInput:
    equity: float
    daily_pnl: float
    peak_equity: float
    open_positions: int
    entry: float
    stop_loss: float
    take_profit: float
    spread_bps: float
    slippage_bps: float
    market_data_age_ms: int
    side: str


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    position_size: float = 0.0
    risk_reward: float = 0.0


class DeterministicRiskEngine:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def evaluate(self, x: RiskInput) -> RiskDecision:
        if x.equity <= 0 or x.peak_equity <= 0:
            return RiskDecision(False, "invalid_equity")

        if x.market_data_age_ms > self.s.max_market_data_age_ms:
            return RiskDecision(False, "stale_market_data")

        if x.spread_bps > self.s.max_spread_bps:
            return RiskDecision(False, "spread_too_wide")

        if x.open_positions >= self.s.max_concurrent_trades:
            return RiskDecision(False, "max_concurrent_trades")

        if x.daily_pnl <= -(x.equity * self.s.max_daily_loss):
            return RiskDecision(False, "daily_loss_limit")

        drawdown = max(0.0, (x.peak_equity - x.equity) / x.peak_equity)
        if drawdown >= self.s.max_drawdown:
            return RiskDecision(False, "max_drawdown")

        stop_distance = abs(x.entry - x.stop_loss)
        target_distance = abs(x.take_profit - x.entry)
        if stop_distance <= 0 or target_distance <= 0:
            return RiskDecision(False, "invalid_stop_or_target")

        if x.side == "LONG" and not (x.stop_loss < x.entry < x.take_profit):
            return RiskDecision(False, "invalid_long_geometry")
        if x.side == "SHORT" and not (x.take_profit < x.entry < x.stop_loss):
            return RiskDecision(False, "invalid_short_geometry")

        rr = target_distance / stop_distance
        if rr < self.s.min_risk_reward:
            return RiskDecision(False, "risk_reward_below_threshold", risk_reward=rr)

        risk_budget = x.equity * self.s.risk_per_trade
        unit_risk = stop_distance + (x.entry * (x.slippage_bps / 10_000.0))
        if unit_risk <= 0:
            return RiskDecision(False, "invalid_unit_risk")

        size = risk_budget / unit_risk
        if size <= 0:
            return RiskDecision(False, "invalid_position_size")

        return RiskDecision(True, "approved", position_size=size, risk_reward=rr)
