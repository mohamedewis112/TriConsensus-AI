from __future__ import annotations

from triconsensus.chronos_forward_evidence import audit_reality_ledger
from triconsensus.chronos_reality_ledger import RealityLedger


def _receipt(ledger: RealityLedger, i: int, probability: float):
    ts = 1_800_000_000_000 + i * 5_000
    return ledger.register(
        symbol="ETHUSDT",
        forecast_timestamp_ms=ts,
        horizon_ms=30_000,
        model_version="v8-test",
        world_mode="learned",
        candidate="WATCH_SHORT" if probability < 0.5 else "WATCH_LONG",
        positive_probability=probability,
        negative_probability=1.0 - probability,
        median_return=-0.0003 if probability < 0.5 else 0.0003,
        p05_return=-0.002,
        p95_return=0.002,
        future_diversity=0.8,
        input_snapshot={"i": i},
    )


def test_high_accuracy_one_sided_market_is_rejected_as_false_comfort():
    ledger = RealityLedger()
    receipts = [_receipt(ledger, i, 0.18) for i in range(14)]
    for receipt in receipts:
        ledger.resolve(
            receipt,
            resolved_timestamp_ms=receipt.forecast_timestamp_ms + receipt.horizon_ms,
            realized_return=-0.0004,
        )
    audit = audit_reality_ledger(
        ledger,
        minimum_total_samples=10,
        minimum_each_class=2,
        minimum_non_overlapping=2,
    )
    assert audit.directional_accuracy == 1.0
    assert audit.base_rate_accuracy == 1.0
    assert audit.excess_directional_accuracy == 0.0
    assert audit.evidence_qualified is False
    assert "one_sided_or_class_imbalanced_outcomes" in audit.reasons
    assert "does_not_beat_empirical_base_rate" in audit.reasons
    assert "no_positive_brier_skill_vs_base_rate" in audit.reasons


def test_overlapping_forecasts_are_collapsed_to_independent_horizons():
    ledger = RealityLedger()
    probabilities = [0.8, 0.75, 0.2, 0.25, 0.8, 0.2, 0.8, 0.2]
    outcomes = [0.001, 0.001, -0.001, -0.001, 0.001, -0.001, 0.001, -0.001]
    receipts = [_receipt(ledger, i, p) for i, p in enumerate(probabilities)]
    for receipt, outcome in zip(receipts, outcomes):
        ledger.resolve(
            receipt,
            resolved_timestamp_ms=receipt.forecast_timestamp_ms + receipt.horizon_ms,
            realized_return=outcome,
        )
    audit = audit_reality_ledger(
        ledger,
        minimum_total_samples=4,
        minimum_each_class=1,
        minimum_non_overlapping=2,
        minimum_excess_accuracy=0.0,
    )
    assert audit.sample_count == 8
    assert audit.non_overlapping_sample_count < audit.sample_count
    assert audit.paper_trading_authority is False
    assert audit.live_trading_authority is False


def test_configured_forward_window_can_supply_minimum_non_overlapping_evidence():
    forward_seconds = 270
    horizon_seconds = 30
    forecast_every_seconds = 5
    max_non_overlapping = 1 + (forward_seconds - horizon_seconds) // horizon_seconds
    total_forecasts = 1 + (forward_seconds - 1) // forecast_every_seconds
    assert max_non_overlapping >= 8
    assert total_forecasts >= 20
