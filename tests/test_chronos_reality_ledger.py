from __future__ import annotations

import pytest

from triconsensus.chronos_reality_ledger import RealityLedger


def _register(ledger: RealityLedger, ts: int, probability: float):
    return ledger.register(
        symbol="ETHUSDT",
        forecast_timestamp_ms=ts,
        horizon_ms=60_000,
        model_version="test-model",
        world_mode="learned",
        candidate="WATCH_LONG" if probability >= 0.5 else "WATCH_SHORT",
        positive_probability=probability,
        negative_probability=1.0 - probability,
        median_return=0.002 if probability >= 0.5 else -0.002,
        p05_return=-0.01,
        p95_return=0.01,
        future_diversity=0.8,
        input_snapshot={"timestamp": ts, "ofi": probability - 0.5},
    )


def test_reality_ledger_hash_chain_and_horizon_lock():
    ledger = RealityLedger()
    first = _register(ledger, 1_800_000_000_000, 0.7)
    second = _register(ledger, 1_800_000_060_000, 0.3)
    assert second.previous_receipt_hash == first.receipt_hash
    assert ledger.verify_chain() is True
    with pytest.raises(ValueError, match="before horizon"):
        ledger.resolve(
            first,
            resolved_timestamp_ms=first.forecast_timestamp_ms + 30_000,
            realized_return=0.004,
        )


def test_reality_ledger_resolves_once_and_scores_calibration():
    ledger = RealityLedger()
    receipts = [
        _register(ledger, 1_800_000_000_000 + i * 120_000, p)
        for i, p in enumerate((0.8, 0.7, 0.2, 0.3))
    ]
    realized = (0.005, 0.003, -0.004, -0.002)
    for receipt, outcome in zip(receipts, realized):
        ledger.resolve(
            receipt,
            resolved_timestamp_ms=receipt.forecast_timestamp_ms + receipt.horizon_ms,
            realized_return=outcome,
        )
    summary = ledger.calibration_summary(bins=5)
    assert summary.sample_count == 4
    assert summary.directional_accuracy == 1.0
    assert summary.interval_coverage == 1.0
    assert summary.mean_brier_score < 0.1
    assert 0.0 <= summary.expected_calibration_error <= 1.0
    with pytest.raises(ValueError, match="already resolved"):
        ledger.resolve(
            receipts[0],
            resolved_timestamp_ms=receipts[0].forecast_timestamp_ms + receipts[0].horizon_ms + 1,
            realized_return=0.001,
        )
