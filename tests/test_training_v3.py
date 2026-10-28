import math

import pytest

from triconsensus.meta_label import score_meta_model
from triconsensus.training_v3 import (
    V3_FEATURE_COLUMNS,
    build_v3_dataset,
    deterministic_candidate,
    trade_path_label,
)


def synthetic_candles(n=1000):
    rows = []
    for i in range(n):
        base = 100 + i * 0.025 + math.sin(i / 8) * 1.2
        rows.append(
            {
                "start_ms": i * 900_000,
                "open": base - 0.08,
                "high": base + 0.45,
                "low": base - 0.45,
                "close": base,
                "volume": 100 + (i % 31) * 2,
                "turnover": base * (100 + (i % 31) * 2),
            }
        )
    return rows


def test_candidate_rule_is_symmetric():
    long = {
        "close": 105,
        "ema_20": 104,
        "ema_50": 100,
        "vwap": 103,
        "rsi_14": 60,
        "macd_hist": 1,
        "adx_14": 25,
    }
    short = {
        "close": 95,
        "ema_20": 96,
        "ema_50": 100,
        "vwap": 97,
        "rsi_14": 40,
        "macd_hist": -1,
        "adx_14": 25,
    }
    assert deterministic_candidate(long) == 1
    assert deterministic_candidate(short) == -1


def test_barrier_tie_scores_conservatively_as_loss():
    candles = synthetic_candles(20)
    candles[1]["high"] = 110
    candles[1]["low"] = 90
    label, net = trade_path_label(
        candles,
        index=0,
        side=1,
        atr=2,
        horizon=3,
    )
    assert label == 0
    assert net < 0


def test_meta_selector_charges_baseline_and_selects_tail():
    score = score_meta_model(
        model_family="test",
        train_probability=[0.1, 0.2, 0.8, 0.9],
        test_probability=[0.1, 0.9, 0.85, 0.2],
        y_train=[0, 0, 1, 1],
        y_test=[0, 1, 1, 0],
        test_net_returns=[-0.01, 0.02, 0.015, -0.005],
        selection_fraction=0.5,
    )
    assert score.trade_count == 2
    assert score.hit_rate == 1.0
    assert score.mean_net_return == pytest.approx(0.0175)


def test_v3_dataset_schema_and_chronology():
    ds = build_v3_dataset(synthetic_candles(), horizon=12, lookback=120)
    assert list(ds.x.columns) == V3_FEATURE_COLUMNS
    assert ds.timestamps == sorted(ds.timestamps)
    assert len(ds.x) >= 300
