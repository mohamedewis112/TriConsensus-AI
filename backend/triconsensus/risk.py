from __future__ import annotations

from dataclasses import dataclass

from .config import Settings


@dataclass(frozen=True)
class RiskInput:
    equity: float
    day_start_equity: float
    daily_pnl: float
    peak_equity: float
    open_positions: int
    current_exposure: float
    correlated_exposure: float
    losing_streak: int
    minutes_since_last_loss: int
    kill_switch_active: bool
    entry: float
    stop_loss: float
    take_profit: float
    spread_bps: float
    slippage_bps: float
    realized_volatility_20: float
    market_data_age_ms: int
    side: str
    position_weight: float = 1.0


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    position_size: float = 0.0
    risk_reward: float = 0.0
    projected_exposure: float = 0.0


class DeterministicRiskEngine:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def evaluate(self, x: RiskInput) -> RiskDecision:
        if x.kill_switch_active:
            return RiskDecision(False, "kill_switch_active")

        if x.equity <= 0 or x.peak_equity <= 0 or x.day_start_equity <= 0:
            return RiskDecision(False, "invalid_equity")

        if x.market_data_age_ms > self.s.max_market_data_age_ms:
            return RiskDecision(False, "stale_market_data")

        if x.spread_bps > self.s.max_spread_bps:
            return RiskDecision(False, "spread_too_wide")

        if x.slippage_bps > self.s.max_slippage_bps:
            return RiskDecision(False, "slippage_too_high")

        if x.realized_volatility_20 > self.s.max_realized_volatility_20:
            return RiskDecision(False, "volatility_too_high")

        if x.open_positions >= self.s.max_concurrent_trades:
            return RiskDecision(False, "max_concurrent_trades")

        if x.losing_streak >= self.s.max_losing_streak:
            return RiskDecision(False, "losing_streak_limit")

        if (
            x.losing_streak > 0
            and x.minutes_since_last_loss < self.s.cooldown_after_loss_minutes
        ):
            return RiskDecision(False, "loss_cooldown_active")

        if x.daily_pnl <= -(x.day_start_equity * self.s.max_daily_loss):
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
        if x.side not in {"LONG", "SHORT"}:
            return RiskDecision(False, "invalid_side")

        rr = target_distance / stop_distance
        if rr < self.s.min_risk_reward:
            return RiskDecision(False, "risk_reward_below_threshold", risk_reward=rr)

        if not 0.0 <= x.position_weight <= 1.0:
            return RiskDecision(False, "adaptive_weight_out_of_bounds")
        if x.position_weight == 0.0:
            return RiskDecision(False, "adaptive_weight_zero")

        risk_budget = x.equity * self.s.risk_per_trade * x.position_weight
        unit_risk = stop_distance + (x.entry * (x.slippage_bps / 10_000.0))
        if unit_risk <= 0:
            return RiskDecision(False, "invalid_unit_risk")

        size = risk_budget / unit_risk
        if size <= 0:
            return RiskDecision(False, "invalid_position_size")

        new_notional = size * x.entry
        projected_exposure = x.current_exposure + new_notional
        if projected_exposure > x.equity * self.s.max_account_exposure:
            return RiskDecision(
                False,
                "max_account_exposure",
                risk_reward=rr,
                projected_exposure=projected_exposure,
            )

        projected_correlated = x.correlated_exposure + new_notional
        if projected_correlated > x.equity * self.s.max_correlated_exposure:
            return RiskDecision(
                False,
                "max_correlated_exposure",
                risk_reward=rr,
                projected_exposure=projected_exposure,
            )

        return RiskDecision(
            True,
            "approved",
            position_size=size,
            risk_reward=rr,
            projected_exposure=projected_exposure,
        )
