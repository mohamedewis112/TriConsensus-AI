from __future__ import annotations

from triconsensus.chronos_reality_ledger import RealityLedger
from triconsensus.chronos_forward_shadow import resolve_due_receipts


def test_forward_receipt_cannot_resolve_until_due_then_resolves_once():
    ledger = RealityLedger()
    receipt = ledger.register(
        symbol="BTCUSDT",
        forecast_timestamp_ms=1_800_000_000_000,
        horizon_ms=30_000,
        model_version="frozen-test",
        world_mode="learned",
        candidate="WATCH_LONG",
        positive_probability=0.7,
        negative_probability=0.3,
        median_return=0.002,
        p05_return=-0.01,
        p95_return=0.01,
        future_diversity=0.8,
        input_snapshot={"mid": 70_000.0},
    )
    pending = {receipt.receipt_hash: (receipt, 70_000.0)}
    assert resolve_due_receipts(
        ledger=ledger,
        pending=pending,
        current_timestamp_ms=1_800_000_020_000,
        current_mid_price=70_050.0,
    ) == 0
    assert len(pending) == 1
    assert resolve_due_receipts(
        ledger=ledger,
        pending=pending,
        current_timestamp_ms=1_800_000_031_000,
        current_mid_price=70_140.0,
    ) == 1
    assert not pending
    assert len(ledger.resolutions) == 1


def test_untrusted_shadow_forecast_can_still_be_logged_without_execution_authority():
    ledger = RealityLedger()
    receipt = ledger.register(
        symbol="ETHUSDT",
        forecast_timestamp_ms=1_800_000_000_000,
        horizon_ms=30_000,
        model_version="untrusted-shadow-test",
        world_mode="learned_shadow_untrusted",
        candidate="SHADOW_FORECAST_UNTRUSTED",
        positive_probability=0.48,
        negative_probability=0.47,
        median_return=0.0,
        p05_return=-0.01,
        p95_return=0.01,
        future_diversity=0.8,
        input_snapshot={"learning_gate":{"calibrated":True,"predictive_skill":False}},
    )
    assert receipt.world_mode == "learned_shadow_untrusted"
    assert receipt.paper_trading_authority is False
    assert receipt.live_trading_authority is False
