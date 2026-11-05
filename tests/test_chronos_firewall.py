from __future__ import annotations

import numpy as np

from triconsensus.chronos_firewall import (
    ParameterPoint,
    deflated_sharpe_probability,
    parameter_plateau,
    probability_of_backtest_overfitting,
    research_firewall,
)


def test_parameter_plateau_rejects_single_magic_number_peak():
    points = [
        ParameterPoint(10, 0.10),
        ParameterPoint(11, 0.12),
        ParameterPoint(12, 0.80),
        ParameterPoint(13, 0.11),
        ParameterPoint(14, 0.09),
        ParameterPoint(15, 0.08),
    ]
    result = parameter_plateau(points)
    assert result.robust is False
    assert result.neighbor_fragility > 0.35


def test_parameter_plateau_accepts_broad_robust_region():
    points = [
        ParameterPoint(10, 0.25),
        ParameterPoint(11, 0.72),
        ParameterPoint(12, 0.80),
        ParameterPoint(13, 0.78),
        ParameterPoint(14, 0.74),
        ParameterPoint(15, 0.30),
    ]
    result = parameter_plateau(points)
    assert result.robust is True
    assert result.plateau_low <= 11
    assert result.plateau_high >= 14


def test_pbo_detects_unstable_trial_selection():
    rng = np.random.default_rng(5)
    blocks = []
    for block in range(8):
        chunk = rng.normal(0, 0.01, size=(40, 8))
        chunk[:, block % 8] += 0.006
        blocks.append(chunk)
    matrix = np.vstack(blocks)
    result = probability_of_backtest_overfitting(matrix, blocks=8)
    assert 0.0 <= result.probability_backtest_overfit <= 1.0
    assert result.splits_evaluated == 70


def test_deflated_sharpe_penalizes_large_number_of_trials():
    rng = np.random.default_rng(9)
    returns = rng.normal(0.0015, 0.01, size=300)
    few = deflated_sharpe_probability(returns, number_of_trials=2, trial_sharpe_std=0.08)
    many = deflated_sharpe_probability(returns, number_of_trials=500, trial_sharpe_std=0.08)
    assert many.expected_max_null_sharpe > few.expected_max_null_sharpe
    assert many.probability <= few.probability


def test_firewall_is_research_only_even_when_candidate_passes_or_fails():
    rng = np.random.default_rng(42)
    n = 320
    candidate = rng.normal(0.0020, 0.009, size=n)
    trials = np.column_stack(
        [
            candidate,
            rng.normal(-0.0002, 0.010, size=n),
            rng.normal(0.0001, 0.010, size=n),
            rng.normal(-0.0001, 0.012, size=n),
        ]
    )
    points = [
        ParameterPoint(10, 0.65),
        ParameterPoint(11, 0.78),
        ParameterPoint(12, 0.82),
        ParameterPoint(13, 0.80),
        ParameterPoint(14, 0.75),
        ParameterPoint(15, 0.50),
    ]
    report = research_firewall(
        candidate_returns=candidate,
        all_trial_returns=trials,
        parameter_points=points,
        maximum_pbo=1.0,
        minimum_deflated_probability=0.50,
    )
    assert report.paper_trading_authority is False
    assert report.live_trading_authority is False
    assert report.sample_count == n
    assert report.trial_count == 4
