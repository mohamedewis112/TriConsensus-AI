from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from typing import Sequence

import numpy as np


CHRONOS_FIREWALL_VERSION = "v8.6-chronos-research-firewall-0"
_EULER_GAMMA = 0.5772156649015329


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _normal_ppf(p: float) -> float:
    """Acklam inverse-normal approximation; accurate enough for research gating."""
    if not 0.0 < p < 1.0:
        if p == 0.0:
            return -math.inf
        if p == 1.0:
            return math.inf
        raise ValueError("p must be in [0,1]")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        )
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    )


def sharpe_ratio(returns: Sequence[float], *, annualization: float = 1.0) -> float:
    values = np.asarray(returns, dtype=float)
    if len(values) < 2:
        return 0.0
    std = float(np.std(values, ddof=1))
    if std < 1e-15:
        return 0.0
    return float(math.sqrt(annualization) * np.mean(values) / std)


def _skew_kurtosis(values: np.ndarray) -> tuple[float, float]:
    if len(values) < 4:
        return 0.0, 3.0
    centered = values - values.mean()
    std = float(np.std(values, ddof=0))
    if std < 1e-15:
        return 0.0, 3.0
    skew = float(np.mean((centered / std) ** 3))
    kurtosis = float(np.mean((centered / std) ** 4))
    return skew, kurtosis


@dataclass(frozen=True)
class DeflatedSharpeResult:
    observed_sharpe: float
    expected_max_null_sharpe: float
    probability: float
    number_of_trials: int
    sample_count: int


def deflated_sharpe_probability(
    returns: Sequence[float],
    *,
    number_of_trials: int,
    trial_sharpe_std: float | None = None,
) -> DeflatedSharpeResult:
    values = np.asarray(returns, dtype=float)
    if len(values) < 20:
        raise ValueError("deflated Sharpe requires at least 20 observations")
    if number_of_trials < 1:
        raise ValueError("number_of_trials must be >=1")
    observed = sharpe_ratio(values)
    skew, kurtosis = _skew_kurtosis(values)
    if trial_sharpe_std is None:
        trial_sharpe_std = 1.0 / math.sqrt(max(2.0, len(values) - 1.0))
    sigma_trials = max(float(trial_sharpe_std), 1e-9)
    if number_of_trials == 1:
        expected_max = 0.0
    else:
        p1 = 1.0 - 1.0 / number_of_trials
        p2 = 1.0 - 1.0 / (number_of_trials * math.e)
        expected_max = sigma_trials * (
            (1.0 - _EULER_GAMMA) * _normal_ppf(p1)
            + _EULER_GAMMA * _normal_ppf(p2)
        )
    denominator = math.sqrt(
        max(
            1e-12,
            1.0
            - skew * observed
            + ((kurtosis - 1.0) / 4.0) * observed * observed,
        )
    )
    z = (observed - expected_max) * math.sqrt(len(values) - 1.0) / denominator
    return DeflatedSharpeResult(
        observed_sharpe=observed,
        expected_max_null_sharpe=expected_max,
        probability=_normal_cdf(z),
        number_of_trials=number_of_trials,
        sample_count=len(values),
    )


@dataclass(frozen=True)
class PBOResult:
    probability_backtest_overfit: float
    splits_evaluated: int
    median_oos_percentile: float
    selected_strategy_counts: tuple[int, ...]


def probability_of_backtest_overfitting(
    performance_matrix: Sequence[Sequence[float]],
    *,
    blocks: int = 8,
) -> PBOResult:
    matrix = np.asarray(performance_matrix, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("performance_matrix must be 2D [time, strategies]")
    observations, strategies = matrix.shape
    if strategies < 2:
        raise ValueError("PBO requires at least two strategy trials")
    if blocks < 4 or blocks % 2:
        raise ValueError("blocks must be even and >=4")
    if observations < blocks * 4:
        raise ValueError("not enough observations for PBO blocks")

    block_indices = np.array_split(np.arange(observations), blocks)
    logits: list[float] = []
    percentiles: list[float] = []
    selected_counts = np.zeros(strategies, dtype=int)
    all_blocks = set(range(blocks))

    for train_blocks in combinations(range(blocks), blocks // 2):
        train_set = set(train_blocks)
        test_blocks = sorted(all_blocks - train_set)
        train_idx = np.concatenate([block_indices[i] for i in sorted(train_set)])
        test_idx = np.concatenate([block_indices[i] for i in test_blocks])
        train_scores = np.array(
            [sharpe_ratio(matrix[train_idx, j]) for j in range(strategies)],
            dtype=float,
        )
        selected = int(np.argmax(train_scores))
        selected_counts[selected] += 1
        test_scores = np.array(
            [sharpe_ratio(matrix[test_idx, j]) for j in range(strategies)],
            dtype=float,
        )
        order = np.argsort(test_scores)
        rank = int(np.where(order == selected)[0][0]) + 1
        percentile = rank / (strategies + 1.0)
        percentiles.append(percentile)
        clipped = min(1.0 - 1e-9, max(1e-9, percentile))
        logits.append(math.log(clipped / (1.0 - clipped)))

    pbo = float(np.mean(np.asarray(logits) < 0.0))
    return PBOResult(
        probability_backtest_overfit=pbo,
        splits_evaluated=len(logits),
        median_oos_percentile=float(np.median(percentiles)),
        selected_strategy_counts=tuple(int(v) for v in selected_counts),
    )


@dataclass(frozen=True)
class ParameterPoint:
    value: float
    metric: float


@dataclass(frozen=True)
class ParameterPlateauResult:
    best_value: float
    best_metric: float
    plateau_low: float
    plateau_high: float
    plateau_fraction_of_range: float
    neighbor_fragility: float
    robust: bool


def parameter_plateau(
    points: Sequence[ParameterPoint],
    *,
    retain_fraction: float = 0.80,
    minimum_plateau_fraction: float = 0.20,
) -> ParameterPlateauResult:
    if len(points) < 5:
        raise ValueError("parameter plateau requires at least five points")
    if not 0.0 < retain_fraction <= 1.0:
        raise ValueError("retain_fraction must be in (0,1]")
    ordered = sorted(points, key=lambda point: point.value)
    metrics = np.array([point.metric for point in ordered], dtype=float)
    best_index = int(np.argmax(metrics))
    best = ordered[best_index]
    if best.metric <= 0:
        return ParameterPlateauResult(
            best_value=best.value,
            best_metric=best.metric,
            plateau_low=best.value,
            plateau_high=best.value,
            plateau_fraction_of_range=0.0,
            neighbor_fragility=1.0,
            robust=False,
        )
    threshold = best.metric * retain_fraction
    left = best_index
    right = best_index
    while left - 1 >= 0 and ordered[left - 1].metric >= threshold:
        left -= 1
    while right + 1 < len(ordered) and ordered[right + 1].metric >= threshold:
        right += 1
    total_range = max(1e-12, ordered[-1].value - ordered[0].value)
    plateau_width = ordered[right].value - ordered[left].value
    plateau_fraction = plateau_width / total_range

    adjacent_metrics: list[float] = []
    if best_index > 0:
        adjacent_metrics.append(ordered[best_index - 1].metric)
    if best_index + 1 < len(ordered):
        adjacent_metrics.append(ordered[best_index + 1].metric)
    if adjacent_metrics:
        neighbor_mean = float(np.mean(adjacent_metrics))
        fragility = max(0.0, (best.metric - neighbor_mean) / abs(best.metric))
    else:
        fragility = 1.0
    robust = plateau_fraction >= minimum_plateau_fraction and fragility <= 0.35
    return ParameterPlateauResult(
        best_value=best.value,
        best_metric=best.metric,
        plateau_low=ordered[left].value,
        plateau_high=ordered[right].value,
        plateau_fraction_of_range=plateau_fraction,
        neighbor_fragility=fragility,
        robust=robust,
    )


@dataclass(frozen=True)
class ResearchFirewallReport:
    version: str
    passed: bool
    reasons: tuple[str, ...]
    deflated_sharpe: DeflatedSharpeResult
    pbo: PBOResult
    plateau: ParameterPlateauResult
    mean_net_return: float
    sample_count: int
    trial_count: int
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def research_firewall(
    *,
    candidate_returns: Sequence[float],
    all_trial_returns: Sequence[Sequence[float]],
    parameter_points: Sequence[ParameterPoint],
    maximum_pbo: float = 0.20,
    minimum_deflated_probability: float = 0.95,
) -> ResearchFirewallReport:
    values = np.asarray(candidate_returns, dtype=float)
    matrix = np.asarray(all_trial_returns, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("all_trial_returns must be [time, trials]")
    if len(values) != matrix.shape[0]:
        raise ValueError("candidate/all-trial observation count mismatch")
    trial_count = matrix.shape[1]
    trial_sharpes = np.array(
        [sharpe_ratio(matrix[:, j]) for j in range(trial_count)],
        dtype=float,
    )
    dsr = deflated_sharpe_probability(
        values,
        number_of_trials=trial_count,
        trial_sharpe_std=float(np.std(trial_sharpes, ddof=1)) if trial_count > 1 else None,
    )
    pbo = probability_of_backtest_overfitting(matrix)
    plateau = parameter_plateau(parameter_points)
    reasons: list[str] = []
    mean_net = float(np.mean(values))
    if mean_net <= 0:
        reasons.append("non_positive_net_expectancy")
    if dsr.probability < minimum_deflated_probability:
        reasons.append("deflated_sharpe_not_significant")
    if pbo.probability_backtest_overfit > maximum_pbo:
        reasons.append("probability_of_backtest_overfit_too_high")
    if not plateau.robust:
        reasons.append("parameter_peak_without_robust_plateau")
    return ResearchFirewallReport(
        version=CHRONOS_FIREWALL_VERSION,
        passed=not reasons,
        reasons=tuple(reasons),
        deflated_sharpe=dsr,
        pbo=pbo,
        plateau=plateau,
        mean_net_return=mean_net,
        sample_count=len(values),
        trial_count=trial_count,
    )
