from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class MetaLabelScore:
    model_family: str
    brier_score: float
    baseline_brier_score: float
    brier_improvement: float
    trade_count: int
    hit_rate: float
    mean_net_return: float
    baseline_mean_net_return: float
    excess_net_return: float


def make_meta_model(model_family: str):
    if model_family == "lightgbm":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=300,
            learning_rate=0.025,
            num_leaves=24,
            random_state=42,
            verbosity=-1,
        )
    if model_family == "xgboost":
        from xgboost import XGBClassifier

        return XGBClassifier(
            n_estimators=300,
            learning_rate=0.025,
            max_depth=4,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss",
        )
    if model_family == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(
            iterations=300,
            learning_rate=0.025,
            depth=6,
            random_seed=42,
            verbose=False,
        )
    raise ValueError(f"unsupported model family: {model_family}")


def score_meta_model(
    *,
    model_family: str,
    train_probability: Any,
    test_probability: Any,
    y_train: Any,
    y_test: Any,
    test_net_returns: Any,
    selection_fraction: float = 0.30,
) -> MetaLabelScore:
    train_p = np.asarray(train_probability, dtype=float)
    test_p = np.asarray(test_probability, dtype=float)
    train_y = np.asarray(y_train, dtype=float)
    test_y = np.asarray(y_test, dtype=float)
    net_returns = np.asarray(test_net_returns, dtype=float)

    if not 0 < selection_fraction < 1:
        raise ValueError("selection_fraction must be between zero and one")
    if len(test_p) != len(test_y) or len(test_y) != len(net_returns):
        raise ValueError("test arrays must have equal length")

    base_probability = float(np.mean(train_y))
    baseline_brier = float(np.mean((test_y - base_probability) ** 2))
    model_brier = float(np.mean((test_y - test_p) ** 2))
    threshold = float(np.quantile(train_p, 1 - selection_fraction))
    selected = test_p >= threshold

    baseline_mean = float(np.mean(net_returns)) if len(net_returns) else 0.0
    if not np.any(selected):
        return MetaLabelScore(
            model_family=model_family,
            brier_score=model_brier,
            baseline_brier_score=baseline_brier,
            brier_improvement=(
                (baseline_brier - model_brier) / baseline_brier
                if baseline_brier > 0
                else 0.0
            ),
            trade_count=0,
            hit_rate=0.0,
            mean_net_return=0.0,
            baseline_mean_net_return=baseline_mean,
            excess_net_return=-baseline_mean,
        )

    selected_returns = net_returns[selected]
    mean_net = float(np.mean(selected_returns))
    return MetaLabelScore(
        model_family=model_family,
        brier_score=model_brier,
        baseline_brier_score=baseline_brier,
        brier_improvement=(
            (baseline_brier - model_brier) / baseline_brier
            if baseline_brier > 0
            else 0.0
        ),
        trade_count=int(np.sum(selected)),
        hit_rate=float(np.mean(selected_returns > 0)),
        mean_net_return=mean_net,
        baseline_mean_net_return=baseline_mean,
        excess_net_return=mean_net - baseline_mean,
    )
