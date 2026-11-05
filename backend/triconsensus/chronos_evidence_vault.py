from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


CHRONOS_EVIDENCE_VAULT_VERSION = "v8.18-multi-window-evidence-vault-0"


@dataclass(frozen=True)
class WindowEvidence:
    window_id: str
    symbol: str
    source: str
    model_family: str
    horizon_seconds: int
    resolved_forecasts: int
    non_overlapping_samples: int
    non_overlapping_hits: int
    positive_count: int
    negative_or_flat_count: int
    brier_skill_score: float | None
    trusted_at_training: bool
    collector_errors: int = 0


@dataclass(frozen=True)
class VaultAudit:
    version: str
    symbol: str
    windows: int
    total_resolved_forecasts: int
    total_non_overlapping_samples: int
    total_non_overlapping_hits: int
    pooled_non_overlapping_accuracy: float
    pooled_positive_count: int
    pooled_negative_or_flat_count: int
    pooled_base_rate_accuracy: float
    exact_binomial_p_value: float
    windows_beating_base_rate: int
    positive_brier_skill_windows: int
    source_consistent: bool
    horizon_consistent: bool
    model_family_consistent: bool
    evidence_qualified: bool
    reasons: tuple[str, ...]
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def _binomial_tail(*, hits: int, trials: int, p: float) -> float:
    if trials <= 0:
        return 1.0
    p = max(0.0, min(1.0, float(p)))
    if p <= 0.0:
        return 0.0 if hits > 0 else 1.0
    if p >= 1.0:
        return 1.0
    return float(sum(
        math.comb(trials, k) * (p ** k) * ((1.0 - p) ** (trials - k))
        for k in range(hits, trials + 1)
    ))


def audit_windows(
    windows: Sequence[WindowEvidence],
    *,
    minimum_windows: int = 4,
    minimum_non_overlapping_samples: int = 40,
    minimum_each_class: int = 10,
    minimum_windows_beating_base_rate: int = 3,
    significance_level: float = 0.05,
) -> VaultAudit:
    if not windows:
        raise ValueError("evidence vault requires at least one window")
    symbols={w.symbol for w in windows}
    if len(symbols) != 1:
        raise ValueError("audit one symbol at a time")
    symbol=next(iter(symbols))

    sources={w.source for w in windows}
    horizons={w.horizon_seconds for w in windows}
    models={w.model_family for w in windows}
    total_resolved=sum(w.resolved_forecasts for w in windows)
    total_n=sum(w.non_overlapping_samples for w in windows)
    total_hits=sum(w.non_overlapping_hits for w in windows)
    positives=sum(w.positive_count for w in windows)
    negatives=sum(w.negative_or_flat_count for w in windows)
    pooled_accuracy=(total_hits/total_n) if total_n else 0.0
    total_classes=max(1,positives+negatives)
    positive_rate=positives/total_classes
    base_rate=max(positive_rate,1.0-positive_rate)
    p_value=_binomial_tail(hits=total_hits,trials=total_n,p=base_rate)

    windows_beating=0
    positive_brier=0
    for w in windows:
        if w.non_overlapping_samples:
            local_acc=w.non_overlapping_hits/w.non_overlapping_samples
            local_total=max(1,w.positive_count+w.negative_or_flat_count)
            local_pos=w.positive_count/local_total
            local_base=max(local_pos,1.0-local_pos)
            if local_acc > local_base:
                windows_beating+=1
        if w.brier_skill_score is not None and w.brier_skill_score > 0:
            positive_brier+=1

    reasons=[]
    if len(windows)<minimum_windows:
        reasons.append("insufficient_independent_windows")
    if total_n<minimum_non_overlapping_samples:
        reasons.append("insufficient_pooled_non_overlapping_samples")
    if positives<minimum_each_class or negatives<minimum_each_class:
        reasons.append("pooled_outcomes_class_imbalanced")
    if windows_beating<minimum_windows_beating_base_rate:
        reasons.append("edge_not_repeated_across_windows")
    if p_value>=significance_level or pooled_accuracy<=base_rate:
        reasons.append("pooled_directional_edge_not_significant")
    if positive_brier<minimum_windows_beating_base_rate:
        reasons.append("probability_skill_not_repeated_across_windows")
    if len(sources)!=1:
        reasons.append("source_definition_changed_across_windows")
    if len(horizons)!=1:
        reasons.append("horizon_changed_across_windows")
    if len(models)!=1:
        reasons.append("model_family_changed_across_windows")
    if any(w.collector_errors for w in windows):
        reasons.append("collector_errors_present")

    return VaultAudit(
        version=CHRONOS_EVIDENCE_VAULT_VERSION,
        symbol=symbol,
        windows=len(windows),
        total_resolved_forecasts=total_resolved,
        total_non_overlapping_samples=total_n,
        total_non_overlapping_hits=total_hits,
        pooled_non_overlapping_accuracy=pooled_accuracy,
        pooled_positive_count=positives,
        pooled_negative_or_flat_count=negatives,
        pooled_base_rate_accuracy=base_rate,
        exact_binomial_p_value=p_value,
        windows_beating_base_rate=windows_beating,
        positive_brier_skill_windows=positive_brier,
        source_consistent=len(sources)==1,
        horizon_consistent=len(horizons)==1,
        model_family_consistent=len(models)==1,
        evidence_qualified=not reasons,
        reasons=tuple(reasons),
    )
