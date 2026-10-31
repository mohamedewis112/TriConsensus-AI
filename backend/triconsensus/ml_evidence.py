from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MLEvidence:
    probability_positive: float
    expected_forward_return: float
    expected_volatility: float
    probability_tp_before_sl: float | None = None
    model_family: str = "baseline"


def neutral_ml_evidence() -> MLEvidence:
    """Safe placeholder until trained walk-forward models exist."""
    return MLEvidence(
        probability_positive=0.5,
        expected_forward_return=0.0,
        expected_volatility=0.0,
        probability_tp_before_sl=None,
        model_family="untrained_neutral_baseline",
    )
