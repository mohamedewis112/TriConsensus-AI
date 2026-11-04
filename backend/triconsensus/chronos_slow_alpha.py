from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .chronos_dual_clock import SlowAlphaEvidence, SlowCandidateLike


CHRONOS_SLOW_ALPHA_VERSION = "v8.21-slow-alpha-stability-0"


@dataclass(frozen=True)
class QuarterEdgeEvidence:
    quarter: str
    available: bool
    trades: int
    hit_rate: float
    mean_net_return: float
    profit_factor: float

    @property
    def passed(self) -> bool:
        return bool(
            self.available
            and self.trades >= 10
            and self.mean_net_return > 0.0
            and self.profit_factor > 1.25
        )


@dataclass(frozen=True)
class SlowAlphaStabilityReport:
    version: str
    alpha_id: str
    quarters_expected: int
    quarters_available: int
    quarters_passed: int
    total_trades: int
    weighted_mean_net_return: float
    full_year_complete: bool
    stability_gate_passed: bool
    reasons: tuple[str, ...]
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def evaluate_quarterly_stability(
    *,
    alpha_id: str,
    quarters: Sequence[QuarterEdgeEvidence],
    required_passes: int = 3,
    expected_quarters: int = 4,
) -> SlowAlphaStabilityReport:
    if expected_quarters < required_passes:
        raise ValueError("expected_quarters must be >= required_passes")
    if len(quarters) != expected_quarters:
        raise ValueError(
            f"expected exactly {expected_quarters} quarter records, got {len(quarters)}"
        )
    available = [row for row in quarters if row.available]
    passed = [row for row in quarters if row.passed]
    total_trades = sum(row.trades for row in available)
    weighted_mean = (
        sum(row.trades * row.mean_net_return for row in available) / total_trades
        if total_trades > 0
        else 0.0
    )
    complete = len(available) == expected_quarters
    reasons: list[str] = []
    if not complete:
        reasons.append("annual_evidence_incomplete")
    if len(passed) < required_passes:
        reasons.append("insufficient_profitable_quarters")
    if weighted_mean <= 0:
        reasons.append("non_positive_weighted_annual_expectancy")

    return SlowAlphaStabilityReport(
        version=CHRONOS_SLOW_ALPHA_VERSION,
        alpha_id=alpha_id,
        quarters_expected=expected_quarters,
        quarters_available=len(available),
        quarters_passed=len(passed),
        total_trades=total_trades,
        weighted_mean_net_return=float(weighted_mean),
        full_year_complete=complete,
        stability_gate_passed=not reasons,
        reasons=tuple(reasons),
    )


def build_slow_alpha_evidence(
    candidate: SlowCandidateLike,
    *,
    alpha_id: str,
    holdout_trades: int,
    holdout_hit_rate: float,
    holdout_mean_net_return: float,
    profit_factor: float,
    profitable_folds: int,
    stability: SlowAlphaStabilityReport,
) -> SlowAlphaEvidence:
    base_gate = (
        holdout_trades >= 30
        and holdout_hit_rate >= 0.52
        and holdout_mean_net_return >= 0.0003
        and profit_factor >= 1.25
        and profitable_folds >= 3
    )
    return SlowAlphaEvidence(
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
        evidence_gate_passed=bool(base_gate and stability.stability_gate_passed),
    )
