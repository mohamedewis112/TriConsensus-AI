from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RegressionScore:
    model_family: str
    mse: float
    mae: float
    rank_ic: float
    trade_count: int
    mean_net_edge: float
    hit_rate: float


def make_regression_model(model_family: str):
    if model_family == "lightgbm":
        from lightgbm import LGBMRegressor

        return LGBMRegressor(
            n_estimators=300,
            learning_rate=0.025,
            num_leaves=24,
            random_state=42,
            verbosity=-1,
        )
    if model_family == "xgboost":
        from xgboost import XGBRegressor

        return XGBRegressor(
            n_estimators=300,
            learning_rate=0.025,
            max_depth=4,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            objective="reg:squarederror",
        )
    if model_family == "catboost":
        from catboost import CatBoostRegressor

        return CatBoostRegressor(
            iterations=300,
            learning_rate=0.025,
            depth=6,
            loss_function="RMSE",
            random_seed=42,
            verbose=False,
        )
    raise ValueError(f"unsupported model family: {model_family}")


def rank_ic(prediction: Any, actual: Any) -> float:
    p = np.asarray(prediction, dtype=float)
    a = np.asarray(actual, dtype=float)
    if len(p) != len(a) or len(p) < 3:
        return 0.0
    p_rank = np.argsort(np.argsort(p)).astype(float)
    a_rank = np.argsort(np.argsort(a)).astype(float)
    if np.std(p_rank) == 0 or np.std(a_rank) == 0:
        return 0.0
    return float(np.corrcoef(p_rank, a_rank)[0, 1])


def evaluate_predictions(
    *,
    model_family: str,
    train_prediction: Any,
    test_prediction: Any,
    test_return: Any,
    round_trip_cost: float,
    tail_fraction: float = 0.10,
) -> RegressionScore:
    train_prediction = np.asarray(train_prediction, dtype=float)
    test_prediction = np.asarray(test_prediction, dtype=float)
    actual = np.asarray(test_return, dtype=float)
    if not 0 < tail_fraction < 0.5:
        raise ValueError("tail_fraction must be between 0 and 0.5")

    lower = float(np.quantile(train_prediction, tail_fraction))
    upper = float(np.quantile(train_prediction, 1 - tail_fraction))
    signal = np.where(test_prediction >= upper, 1, np.where(test_prediction <= lower, -1, 0))
    traded = signal != 0

    mse = float(np.mean((test_prediction - actual) ** 2))
    mae = float(np.mean(np.abs(test_prediction - actual)))
    ic = rank_ic(test_prediction, actual)

    if not np.any(traded):
        return RegressionScore(model_family, mse, mae, ic, 0, 0.0, 0.0)

    net = (signal[traded] * actual[traded]) - round_trip_cost
    return RegressionScore(
        model_family=model_family,
        mse=mse,
        mae=mae,
        rank_ic=ic,
        trade_count=int(np.sum(traded)),
        mean_net_edge=float(np.mean(net)),
        hit_rate=float(np.mean(net > 0)),
    )
