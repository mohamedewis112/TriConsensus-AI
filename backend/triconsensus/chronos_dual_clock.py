from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .chronos import MarketState
from .chronos_learning import (
    LearningCalibrationReport,
    ProbabilisticTransitionEnsemble,
    TransitionForecast,
)
from .v7_signal_lab import V7Candidate


CHRONOS_DUAL_CLOCK_VERSION = "v8.18-dual-clock-0"


@dataclass(frozen=True)
class SlowAlphaEvidence:
    alpha_id: str
    species: str
    side: str
    confidence: float
    regime_key: str
    holdout_trades: int
    holdout_hit_rate: float
    holdout_mean_net_return: float
    profit_factor: float
    profitable_folds: int
    evidence_gate_passed: bool
    research_only: bool = True

    @classmethod
    def from_v7(
        cls,
        candidate: V7Candidate,
        *,
        alpha_id: str,
        holdout_trades: int,
        holdout_hit_rate: float,
        holdout_mean_net_return: float,
        profit_factor: float,
        profitable_folds: int,
    ) -> "SlowAlphaEvidence":
        gate = (
            holdout_trades >= 30
            and holdout_hit_rate >= 0.52
            and holdout_mean_net_return >= 0.0003
            and profit_factor >= 1.25
            and profitable_folds >= 3
        )
        return cls(
            alpha_id=alpha_id,
            species=candidate.species,
            side=candidate.side,
            confidence=candidate.confidence,
            regime_key=candidate.regime_key,
            holdout_trades=holdout_trades,
            holdout_hit_rate=holdout_hit_rate,
            holdout_mean_net_return=holdout_mean_net_return,
            profit_factor=profit_factor,
            profitable_folds=profitable_folds,
            evidence_gate_passed=gate,
        )


@dataclass(frozen=True)
class HeadTrust:
    price_head_trusted: bool
    orderflow_head_trusted: bool
    liquidity_head_trusted: bool
    fast_execution_trusted: bool
    return_relative_skill: float
    orderflow_relative_skill: float
    liquidity_relative_skill: float
    return_interval_coverage: float
    orderflow_interval_coverage: float
    liquidity_interval_coverage: float
    research_only: bool = True


@dataclass(frozen=True)
class DualClockDecision:
    version: str
    alpha_id: str
    verdict: str
    slow_side: str
    slow_alpha_validated: bool
    fast_execution_trusted: bool
    price_head_trusted: bool
    flow_alignment: float
    predicted_liquidity_change: float
    spread_bps: float
    reasons: tuple[str, ...]
    fast_forecast: TransitionForecast | None
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def head_specific_trust(
    report: LearningCalibrationReport | None,
) -> HeadTrust:
    if report is None:
        return HeadTrust(
            price_head_trusted=False,
            orderflow_head_trusted=False,
            liquidity_head_trusted=False,
            fast_execution_trusted=False,
            return_relative_skill=0.0,
            orderflow_relative_skill=0.0,
            liquidity_relative_skill=0.0,
            return_interval_coverage=0.0,
            orderflow_interval_coverage=0.0,
            liquidity_interval_coverage=0.0,
        )
    targets: Mapping[str, object] = {
        target.target: target for target in report.targets
    }
    ret = targets["next_return"]
    flow = targets["next_order_flow"]
    liq = targets["next_liquidity_change"]
    price_ok = bool(
        report.predictive_skill
        and ret.relative_skill > 0.0
        and ret.interval_coverage >= 0.72
    )
    flow_ok = bool(
        flow.relative_skill > 0.0
        and flow.interval_coverage >= 0.72
    )
    liq_ok = bool(
        liq.relative_skill > 0.0
        and liq.interval_coverage >= 0.72
    )
    return HeadTrust(
        price_head_trusted=price_ok,
        orderflow_head_trusted=flow_ok,
        liquidity_head_trusted=liq_ok,
        fast_execution_trusted=flow_ok and liq_ok,
        return_relative_skill=float(ret.relative_skill),
        orderflow_relative_skill=float(flow.relative_skill),
        liquidity_relative_skill=float(liq.relative_skill),
        return_interval_coverage=float(ret.interval_coverage),
        orderflow_interval_coverage=float(flow.interval_coverage),
        liquidity_interval_coverage=float(liq.interval_coverage),
    )


class DualClockChronos:
    """Separate slow directional alpha from fast execution intelligence.

    The slow clock owns economic direction and must pass the frozen V7 evidence gate.
    The fast clock may veto/timing-filter an already validated slow alpha, but can never
    create a directional trade by itself.
    """

    def __init__(
        self,
        transition_model: ProbabilisticTransitionEnsemble,
        *,
        adverse_flow_threshold: float = -0.15,
        max_liquidity_contraction: float = -0.25,
        max_spread_bps: float = 8.0,
    ):
        self.transition_model = transition_model
        self.adverse_flow_threshold = adverse_flow_threshold
        self.max_liquidity_contraction = max_liquidity_contraction
        self.max_spread_bps = max_spread_bps

    def assess(
        self,
        *,
        slow_alpha: SlowAlphaEvidence,
        state: MarketState,
    ) -> DualClockDecision:
        reasons: list[str] = []
        trust = head_specific_trust(self.transition_model.calibration_report)

        if not slow_alpha.evidence_gate_passed:
            return DualClockDecision(
                version=CHRONOS_DUAL_CLOCK_VERSION,
                alpha_id=slow_alpha.alpha_id,
                verdict="REJECT_SLOW_ALPHA",
                slow_side=slow_alpha.side,
                slow_alpha_validated=False,
                fast_execution_trusted=trust.fast_execution_trusted,
                price_head_trusted=trust.price_head_trusted,
                flow_alignment=0.0,
                predicted_liquidity_change=0.0,
                spread_bps=state.spread_bps,
                reasons=("slow_alpha_failed_frozen_evidence_gate",),
                fast_forecast=None,
            )

        if not trust.fast_execution_trusted:
            return DualClockDecision(
                version=CHRONOS_DUAL_CLOCK_VERSION,
                alpha_id=slow_alpha.alpha_id,
                verdict="HOLD_FAST_EVIDENCE",
                slow_side=slow_alpha.side,
                slow_alpha_validated=True,
                fast_execution_trusted=False,
                price_head_trusted=trust.price_head_trusted,
                flow_alignment=0.0,
                predicted_liquidity_change=0.0,
                spread_bps=state.spread_bps,
                reasons=("fast_orderflow_or_liquidity_head_not_better_than_naive_baseline",),
                fast_forecast=None,
            )

        forecast = self.transition_model.predict(state)
        direction = 1.0 if slow_alpha.side == "LONG" else -1.0
        flow_alignment = direction * forecast.next_order_flow_mean

        if flow_alignment < self.adverse_flow_threshold:
            reasons.append("predicted_orderflow_opposes_slow_alpha")
        if forecast.next_liquidity_change_mean < self.max_liquidity_contraction:
            reasons.append("predicted_liquidity_contraction")
        if state.spread_bps > self.max_spread_bps:
            reasons.append("spread_too_wide")

        if reasons:
            verdict = "VETO_FAST_EXECUTION"
        else:
            verdict = (
                "SHADOW_TIMING_LONG"
                if slow_alpha.side == "LONG"
                else "SHADOW_TIMING_SHORT"
            )

        return DualClockDecision(
            version=CHRONOS_DUAL_CLOCK_VERSION,
            alpha_id=slow_alpha.alpha_id,
            verdict=verdict,
            slow_side=slow_alpha.side,
            slow_alpha_validated=True,
            fast_execution_trusted=True,
            price_head_trusted=trust.price_head_trusted,
            flow_alignment=float(flow_alignment),
            predicted_liquidity_change=float(
                forecast.next_liquidity_change_mean
            ),
            spread_bps=state.spread_bps,
            reasons=tuple(reasons),
            fast_forecast=forecast,
        )
