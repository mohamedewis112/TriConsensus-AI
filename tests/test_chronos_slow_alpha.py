from __future__ import annotations

from dataclasses import dataclass

from triconsensus.chronos_slow_alpha import (
    QuarterEdgeEvidence,
    build_slow_alpha_evidence,
    evaluate_quarterly_stability,
)


@dataclass(frozen=True)
class Candidate:
    species: str
    side: str
    confidence: float
    regime_key: str


def test_current_eth_lead_is_rejected_when_q1_missing_and_only_two_of_three_pass():
    quarters = (
        QuarterEdgeEvidence("Q1", False, 0, 0.0, 0.0, 0.0),
        QuarterEdgeEvidence("Q2", True, 43, 0.5349, 0.0041296, 2.67585),
        QuarterEdgeEvidence("Q3", True, 41, 0.4390, 0.0010813, 1.46306),
        QuarterEdgeEvidence("Q4", True, 31, 0.4194, -0.0025165, 0.48560),
    )
    stability = evaluate_quarterly_stability(
        alpha_id="eth_trend_breakout_short_trenddown_midvol_mixed",
        quarters=quarters,
    )
    assert stability.quarters_available == 3
    assert stability.quarters_passed == 2
    assert stability.full_year_complete is False
    assert stability.stability_gate_passed is False
    assert "annual_evidence_incomplete" in stability.reasons
    assert "insufficient_profitable_quarters" in stability.reasons

    evidence = build_slow_alpha_evidence(
        Candidate(
            species="trend_breakout",
            side="SHORT",
            confidence=0.70,
            regime_key="trend_down|mid_vol|momentum_mixed",
        ),
        alpha_id=stability.alpha_id,
        holdout_trades=115,
        holdout_hit_rate=0.47,
        holdout_mean_net_return=0.001251,
        profit_factor=1.30,
        profitable_folds=3,
        stability=stability,
    )
    assert evidence.evidence_gate_passed is False


def test_three_of_four_positive_quarters_can_pass_only_when_full_year_complete():
    quarters = (
        QuarterEdgeEvidence("Q1", True, 30, 0.56, 0.0010, 1.40),
        QuarterEdgeEvidence("Q2", True, 35, 0.54, 0.0012, 1.55),
        QuarterEdgeEvidence("Q3", True, 28, 0.51, 0.0008, 1.35),
        QuarterEdgeEvidence("Q4", True, 32, 0.44, -0.0004, 0.90),
    )
    report = evaluate_quarterly_stability(
        alpha_id="synthetic_candidate",
        quarters=quarters,
    )
    assert report.full_year_complete is True
    assert report.quarters_passed == 3
    assert report.weighted_mean_net_return > 0
    assert report.stability_gate_passed is True
