from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelScore:
    model_family: str
    accuracy: float
    brier_score: float


def make_binary_model(model_family: str):
    if model_family == "lightgbm":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=250,
            learning_rate=0.03,
            max_depth=-1,
            random_state=42,
            verbosity=-1,
        )

    if model_family == "xgboost":
        from xgboost import XGBClassifier

        return XGBClassifier(
            n_estimators=250,
            learning_rate=0.03,
            max_depth=5,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss",
        )

    if model_family == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(
            iterations=250,
            learning_rate=0.03,
            depth=6,
            random_seed=42,
            verbose=False,
        )

    raise ValueError(f"unsupported model family: {model_family}")


def compare_binary_models(
    x_train: Any,
    y_train: Any,
    x_test: Any,
    y_test: Any,
) -> list[ModelScore]:
    """
    Compare model families on one already-isolated out-of-sample window.

    The caller is responsible for chronological walk-forward windows.
    Test data is never used for fitting or hyperparameter selection.
    """
    from sklearn.metrics import accuracy_score, brier_score_loss

    results: list[ModelScore] = []
    for name in ("lightgbm", "xgboost", "catboost"):
        model = make_binary_model(name)
        model.fit(x_train, y_train)
        probability = model.predict_proba(x_test)[:, 1]
        prediction = (probability >= 0.5).astype(int)
        results.append(
            ModelScore(
                model_family=name,
                accuracy=float(accuracy_score(y_test, prediction)),
                brier_score=float(brier_score_loss(y_test, probability)),
            )
        )
    return sorted(results, key=lambda x: (x.brier_score, -x.accuracy))
