import math

from triconsensus.training import (
    ML_FEATURE_COLUMNS,
    build_training_dataset,
    ml_vector,
)


def candles(n=220):
    rows = []
    for i in range(n):
        base = 100 + (i * 0.03) + math.sin(i / 7)
        rows.append(
            {
                "start_ms": i * 900_000,
                "open": base - 0.1,
                "high": base + 0.6,
                "low": base - 0.6,
                "close": base,
                "volume": 100 + (i % 17) * 3,
                "turnover": base * 100,
            }
        )
    return rows


def test_ml_vector_has_stable_feature_schema():
    dataset = build_training_dataset(
        candles(),
        horizon=4,
        lookback=80,
        positive_return_threshold=0.0,
    )
    assert list(dataset.x.columns) == ML_FEATURE_COLUMNS
    assert len(dataset.x) == 220 - 80 - 4 + 1
    assert len(dataset.y) == len(dataset.x)
    assert dataset.timestamps == sorted(dataset.timestamps)


def test_ml_vector_is_scale_normalized_for_price_features():
    features = {
        "close": 100,
        "ema_20": 101,
        "ema_50": 99,
        "sma_20": 100,
        "rsi_14": 50,
        "macd": 1,
        "macd_hist": 0.2,
        "atr_14": 2,
        "adx_14": 30,
        "vwap": 100.5,
        "stoch_k": 60,
        "stoch_d": 55,
        "roc_10": 0.01,
        "realized_vol_20": 0.02,
        "volume_z_20": 1.2,
        "support_20": 95,
        "resistance_20": 105,
        "atr_normalized_move": 0.5,
    }
    vector = ml_vector(features)
    assert list(vector) == ML_FEATURE_COLUMNS
    assert vector["ema20_distance"] == 0.01
    assert vector["atr_normalized"] == 0.02
