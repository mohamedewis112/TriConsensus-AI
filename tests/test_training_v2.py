import math

from triconsensus.ml_regression import evaluate_predictions, rank_ic
from triconsensus.training_v2 import V2_FEATURE_COLUMNS, build_v2_dataset


def synthetic_candles(n=620):
    rows = []
    for i in range(n):
        base = 100 + i * 0.02 + math.sin(i / 9) * 0.8
        rows.append(
            {
                "start_ms": i * 900_000,
                "open": base - 0.05,
                "high": base + 0.4,
                "low": base - 0.4,
                "close": base,
                "volume": 100 + (i % 23),
                "turnover": base * (100 + (i % 23)),
            }
        )
    return rows


def test_v2_dataset_is_chronological_and_schema_locked():
    ds = build_v2_dataset(synthetic_candles(), horizon=4, lookback=240)
    assert list(ds.x.columns) == V2_FEATURE_COLUMNS
    assert ds.timestamps == sorted(ds.timestamps)
    assert len(ds.x) == 620 - 240 - 4 + 1


def test_rank_ic_detects_ordering():
    assert rank_ic([1, 2, 3, 4], [10, 20, 30, 40]) > 0.99
    assert rank_ic([4, 3, 2, 1], [10, 20, 30, 40]) < -0.99


def test_selective_edge_charges_round_trip_cost():
    score = evaluate_predictions(
        model_family="test",
        train_prediction=[-2, -1, 0, 1, 2],
        test_prediction=[-3, 0, 3],
        test_return=[-0.01, 0.0, 0.01],
        round_trip_cost=0.0015,
        tail_fraction=0.2,
    )
    assert score.trade_count == 2
    assert score.mean_net_edge > 0
