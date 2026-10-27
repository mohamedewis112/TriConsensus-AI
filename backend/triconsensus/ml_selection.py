from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelScore:
    model_family: str
    accuracy: float
    brier_score: float


def compare_binary_models(
    x_train: Any,
    y_train: Any,
    x_test: Any,
    y_test: Any,
) -> list[ModelScore]:
    """
    Compare model families on one already-isolated out-of-sample window.

    The caller is responsible for constructing chronological walk-forward windows.
    Test data is never used for fitting or hyperparameter selection.
    """
    from sklearn.metrics import accuracy_score, brier_score_loss

    from lightgbm import LGBMClassifier
    from xgboost import XGBClassifier
    from catboost import CatBoostClassifier

    models = {
        "lightgbm": LGBMClassifier(
            n_estimators=200,
            learning_rate=0.03,
            max_depth=-1,
            random_state=42,
            verbosity=-1,
        ),
        "xgboost": XGBClassifier(
            n_estimators=200,
            learning_rate=0.03,
            max_depth=5,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss",
        ),
        "catboost": CatBoostClassifier(
            iterations=200,
            learning_rate=0.03,
            depth=6,
            random_seed=42,
            verbose=False,
        ),
    }

    results: list[ModelScore] = []
    for name, model in models.items():
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
