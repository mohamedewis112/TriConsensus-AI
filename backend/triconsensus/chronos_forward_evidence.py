from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np

from .chronos_reality_ledger import ForecastReceipt, OutcomeResolution, RealityLedger


CHRONOS_FORWARD_EVIDENCE_VERSION = "v8.17-forward-evidence-auditor-significance-0"


@dataclass(frozen=True)
class ForwardEvidenceAudit:
    version: str
    sample_count: int
    positive_count: int
    negative_or_flat_count: int
    directional_accuracy: float
    base_rate_accuracy: float
    excess_directional_accuracy: float
    mean_brier_score: float
    baseline_brier_score: float
    brier_skill_score: float | None
    non_overlapping_sample_count: int
    non_overlapping_directional_accuracy: float
    non_overlapping_base_rate_accuracy: float
    class_balance_qualified: bool
    independence_qualified: bool
    sample_size_qualified: bool
    beats_base_rate: bool
    brier_skill_qualified: bool
    provisional_forward_signal: bool
    non_overlapping_hits: int
    exact_binomial_p_value: float
    statistical_sample_qualified: bool
    statistically_significant: bool
    evidence_qualified: bool
    reasons: tuple[str, ...]
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def _base_rate_accuracy(actual_positive: np.ndarray) -> float:
    if len(actual_positive) == 0:
        return 0.0
    p = float(np.mean(actual_positive))
    return max(p, 1.0 - p)

def _one_sided_binomial_p_value(*, hits: int, trials: int, null_accuracy: float) -> float:
    if trials <= 0:
        return 1.0
    p = min(1.0, max(0.0, float(null_accuracy)))
    if hits <= 0:
        return 1.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    return float(
        sum(
            math.comb(trials, k) * (p ** k) * ((1.0 - p) ** (trials - k))
            for k in range(hits, trials + 1)
        )
    )



def _non_overlapping_pairs(
    pairs: Sequence[tuple[ForecastReceipt, OutcomeResolution]],
) -> list[tuple[ForecastReceipt, OutcomeResolution]]:
    selected: list[tuple[ForecastReceipt, OutcomeResolution]] = []
    last_horizon_end = -1
    for receipt, resolution in sorted(
        pairs,
        key=lambda pair: pair[0].forecast_timestamp_ms,
    ):
        if receipt.forecast_timestamp_ms < last_horizon_end:
            continue
        selected.append((receipt, resolution))
        last_horizon_end = receipt.forecast_timestamp_ms + receipt.horizon_ms
    return selected


def audit_reality_ledger(
    ledger: RealityLedger,
    *,
    minimum_total_samples: int = 20,
    minimum_each_class: int = 5,
    minimum_non_overlapping: int = 8,
    minimum_excess_accuracy: float = 0.05,
    minimum_statistical_non_overlapping: int = 20,
    significance_level: float = 0.05,
) -> ForwardEvidenceAudit:
    resolutions = {r.receipt_hash: r for r in ledger.resolutions}
    pairs = [
        (receipt, resolutions[receipt.receipt_hash])
        for receipt in ledger.receipts
        if receipt.receipt_hash in resolutions
    ]
    if not pairs:
        raise ValueError("no resolved forecasts to audit")

    actual_positive = np.asarray(
        [resolution.realized_return > 0 for _, resolution in pairs],
        dtype=bool,
    )
    directional_hits = np.asarray(
        [resolution.directional_hit for _, resolution in pairs],
        dtype=bool,
    )
    directional_accuracy = float(np.mean(directional_hits))
    base_rate = _base_rate_accuracy(actual_positive)
    excess = directional_accuracy - base_rate

    model_brier = float(
        np.mean([resolution.brier_score for _, resolution in pairs])
    )
    empirical_positive = float(np.mean(actual_positive))
    baseline_brier = float(
        np.mean(
            [
                (empirical_positive - (1.0 if is_positive else 0.0)) ** 2
                for is_positive in actual_positive
            ]
        )
    )
    if baseline_brier <= 1e-15:
        brier_skill = None
        brier_skill_qualified = False
    else:
        brier_skill = 1.0 - (model_brier / baseline_brier)
        brier_skill_qualified = brier_skill > 0.0

    non_overlap = _non_overlapping_pairs(pairs)
    non_overlap_actual = np.asarray(
        [resolution.realized_return > 0 for _, resolution in non_overlap],
        dtype=bool,
    )
    non_overlap_hits = np.asarray(
        [resolution.directional_hit for _, resolution in non_overlap],
        dtype=bool,
    )
    non_overlap_accuracy = (
        float(np.mean(non_overlap_hits)) if len(non_overlap_hits) else 0.0
    )
    non_overlap_base = _base_rate_accuracy(non_overlap_actual)
    non_overlap_hit_count = int(np.sum(non_overlap_hits))
    binomial_p_value = _one_sided_binomial_p_value(
        hits=non_overlap_hit_count,
        trials=len(non_overlap_hits),
        null_accuracy=non_overlap_base,
    )

    positive_count = int(np.sum(actual_positive))
    negative_count = len(actual_positive) - positive_count
    class_balance_ok = (
        positive_count >= minimum_each_class
        and negative_count >= minimum_each_class
    )
    independence_ok = len(non_overlap) >= minimum_non_overlapping
    sample_ok = len(pairs) >= minimum_total_samples
    beats_base = excess >= minimum_excess_accuracy
    statistical_sample_ok = len(non_overlap) >= minimum_statistical_non_overlapping
    statistically_significant = (
        statistical_sample_ok
        and binomial_p_value < significance_level
        and non_overlap_accuracy > non_overlap_base
    )

    provisional_reasons: list[str] = []
    if not sample_ok:
        provisional_reasons.append("insufficient_total_forward_samples")
    if not class_balance_ok:
        provisional_reasons.append("one_sided_or_class_imbalanced_outcomes")
    if not independence_ok:
        provisional_reasons.append("insufficient_non_overlapping_horizons")
    if not beats_base:
        provisional_reasons.append("does_not_beat_empirical_base_rate")
    if not brier_skill_qualified:
        provisional_reasons.append("no_positive_brier_skill_vs_base_rate")

    provisional = not provisional_reasons
    reasons = list(provisional_reasons)
    if not statistical_sample_ok:
        reasons.append("insufficient_non_overlapping_samples_for_significance")
    elif not statistically_significant:
        reasons.append("directional_edge_not_statistically_significant_vs_base_rate")

    return ForwardEvidenceAudit(
        version=CHRONOS_FORWARD_EVIDENCE_VERSION,
        sample_count=len(pairs),
        positive_count=positive_count,
        negative_or_flat_count=negative_count,
        directional_accuracy=directional_accuracy,
        base_rate_accuracy=base_rate,
        excess_directional_accuracy=excess,
        mean_brier_score=model_brier,
        baseline_brier_score=baseline_brier,
        brier_skill_score=brier_skill,
        non_overlapping_sample_count=len(non_overlap),
        non_overlapping_directional_accuracy=non_overlap_accuracy,
        non_overlapping_base_rate_accuracy=non_overlap_base,
        class_balance_qualified=class_balance_ok,
        independence_qualified=independence_ok,
        sample_size_qualified=sample_ok,
        beats_base_rate=beats_base,
        brier_skill_qualified=brier_skill_qualified,
        provisional_forward_signal=provisional,
        non_overlapping_hits=non_overlap_hit_count,
        exact_binomial_p_value=binomial_p_value,
        statistical_sample_qualified=statistical_sample_ok,
        statistically_significant=statistically_significant,
        evidence_qualified=(provisional and statistically_significant),
        reasons=tuple(reasons),
    )
