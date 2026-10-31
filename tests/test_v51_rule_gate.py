import numpy as np
import pandas as pd

from triconsensus.v51_rule_gate import (
    RuleThresholds,
    calibrate_thresholds,
    evaluate_rule,
    execute_frozen_rule,
)


def synthetic_frame(n=2400):
    feature = np.linspace(-2.0, 2.0, n)
    close = 100 * np.exp(np.linspace(0, 0.2, n))
    return pd.DataFrame(
        {
            "start_ms": np.arange(n) * 900_000,
            "close": close,
            "global_long_short_change_1h": feature,
        }
    )


def test_calibration_uses_fixed_deciles():
    thresholds = calibrate_thresholds(synthetic_frame())
    assert thresholds.low < 0
    assert thresholds.high > 0
    assert thresholds.low < thresholds.high


def test_frozen_rule_maps_negative_relation_to_long_and_short():
    frame = synthetic_frame(300)
    thresholds = RuleThresholds(low=-1.0, high=1.0)
    trades = execute_frozen_rule(
        frame,
        thresholds,
        horizon=12,
        round_trip_cost=0.0,
    )
    assert trades
    assert any(row["side"] == "LONG" for row in trades)
    assert any(row["side"] == "SHORT" for row in trades)
    for left, right in zip(trades, trades[1:]):
        assert right["entry_index"] > left["exit_index"]


def test_rule_gate_qualifies_stable_positive_returns():
    trades = []
    for i in range(120):
        trades.append(
            {
                "side": "LONG" if i % 2 == 0 else "SHORT",
                "gross_return": 0.0015,
                "net_return": 0.001,
            }
        )
    report = evaluate_rule(trades)
    assert report["qualified"] is True
    assert report["profitable_folds"] == 4
    assert report["bootstrap_lcb_95"] > 0


def test_rule_gate_rejects_unstable_or_negative_returns():
    trades = []
    for i in range(120):
        value = 0.003 if i < 30 else -0.001
        trades.append(
            {
                "side": "LONG",
                "gross_return": value + 0.0015,
                "net_return": value,
            }
        )
    report = evaluate_rule(trades)
    assert report["qualified"] is False
