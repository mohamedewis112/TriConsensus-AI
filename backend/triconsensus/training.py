from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import pickle

import numpy as np
import pandas as pd

from .archive_data import BybitPublicArchiveClient
from .config import Settings
from .features import build_features
from .market_data import BybitPublicClient
from .ml_selection import compare_binary_models, make_binary_model
from .walk_forward import walk_forward_splits


ML_FEATURE_COLUMNS = [
    "ema20_distance",
    "ema50_distance",
    "sma20_distance",
    "rsi_14",
    "macd_normalized",
    "macd_hist_normalized",
    "atr_normalized",
    "adx_14",
    "vwap_distance",
    "stoch_k",
    "stoch_d",
    "roc_10",
    "realized_vol_20",
    "volume_z_20",
    "support_distance",
    "resistance_distance",
    "atr_normalized_move",
]


@dataclass(frozen=True)
class TrainingDataset:
    x: pd.DataFrame
    y: pd.Series
    forward_returns: pd.Series
    timestamps: list[int]


def ml_vector(features: dict) -> dict[str, float]:
    close = float(features["close"])
    support = float(features["support_20"])
    resistance = float(features["resistance_20"])
    if close <= 0 or support <= 0 or resistance <= 0:
        raise ValueError("invalid price feature")

    vector = {
        "ema20_distance": (float(features["ema_20"]) / close) - 1,
        "ema50_distance": (float(features["ema_50"]) / close) - 1,
        "sma20_distance": (float(features["sma_20"]) / close) - 1,
        "rsi_14": float(features["rsi_14"]) / 100,
        "macd_normalized": float(features["macd"]) / close,
        "macd_hist_normalized": float(features["macd_hist"]) / close,
        "atr_normalized": float(features["atr_14"]) / close,
        "adx_14": float(features["adx_14"]) / 100,
        "vwap_distance": (float(features["vwap"]) / close) - 1,
        "stoch_k": float(features["stoch_k"]) / 100,
        "stoch_d": float(features["stoch_d"]) / 100,
        "roc_10": float(features["roc_10"]),
        "realized_vol_20": float(features["realized_vol_20"]),
        "volume_z_20": float(features["volume_z_20"]),
        "support_distance": (close / support) - 1,
        "resistance_distance": (resistance / close) - 1,
        "atr_normalized_move": float(features["atr_normalized_move"]),
    }
    if not all(np.isfinite(value) for value in vector.values()):
        raise ValueError("ML vector contains non-finite value")
    return vector


def build_training_dataset(
    candles: list[dict],
    *,
    horizon: int = 4,
    lookback: int = 80,
    positive_return_threshold: float = 0.0015,
) -> TrainingDataset:
    if horizon <= 0 or lookback < 60:
        raise ValueError("invalid training horizon/lookback")
    if len(candles) < lookback + horizon + 100:
        raise ValueError("not enough candles for training")

    rows: list[dict[str, float]] = []
    labels: list[int] = []
    returns: list[float] = []
    timestamps: list[int] = []

    for index in range(lookback - 1, len(candles) - horizon):
        window = candles[index - lookback + 1 : index + 1]
        frame = pd.DataFrame(window)[["open", "high", "low", "close", "volume"]]
        features = build_features(frame)
        vector = ml_vector(features)

        current_close = float(candles[index]["close"])
        future_close = float(candles[index + horizon]["close"])
        forward_return = (future_close / current_close) - 1.0

        rows.append(vector)
        returns.append(forward_return)
        labels.append(int(forward_return > positive_return_threshold))
        timestamps.append(int(candles[index]["start_ms"]))

    x = pd.DataFrame(rows, columns=ML_FEATURE_COLUMNS)
    y = pd.Series(labels, name="target_positive", dtype="int64")
    forward_returns = pd.Series(returns, name="forward_return", dtype="float64")

    if x.isna().any().any():
        raise ValueError("training features contain NaN")
    if y.nunique() < 2:
        raise ValueError("training target has only one class")

    return TrainingDataset(
        x=x,
        y=y,
        forward_returns=forward_returns,
        timestamps=timestamps,
    )


def evaluate_walk_forward(dataset: TrainingDataset) -> dict:
    n = len(dataset.x)
    train_size = max(500, int(n * 0.55))
    test_size = max(150, int(n * 0.10))
    if train_size + test_size > n:
        train_size = max(300, int(n * 0.65))
        test_size = n - train_size

    splits = walk_forward_splits(
        n,
        train_size=train_size,
        test_size=test_size,
        step_size=test_size,
    )
    if not splits:
        raise ValueError("dataset too small for walk-forward validation")

    aggregate: dict[str, list[dict[str, float]]] = defaultdict(list)
    baseline_rows: list[dict[str, float]] = []
    fold_reports: list[dict] = []

    for fold_number, split in enumerate(splits, start=1):
        x_train = dataset.x.iloc[split.train_start : split.train_end]
        y_train = dataset.y.iloc[split.train_start : split.train_end]
        x_test = dataset.x.iloc[split.test_start : split.test_end]
        y_test = dataset.y.iloc[split.test_start : split.test_end]

        if y_train.nunique() < 2 or y_test.nunique() < 2:
            continue

        train_positive_rate = float(y_train.mean())
        majority_class = int(train_positive_rate >= 0.5)
        baseline_accuracy = float((y_test == majority_class).mean())
        baseline_brier = float(
            np.mean((y_test.to_numpy(dtype=float) - train_positive_rate) ** 2)
        )
        baseline = {
            "accuracy": baseline_accuracy,
            "brier_score": baseline_brier,
            "train_positive_rate": train_positive_rate,
        }
        baseline_rows.append(baseline)

        scores = compare_binary_models(x_train, y_train, x_test, y_test)
        score_payload = [asdict(score) for score in scores]
        for score in score_payload:
            aggregate[score["model_family"]].append(score)

        fold_reports.append(
            {
                "fold": fold_number,
                "train_start_ms": dataset.timestamps[split.train_start],
                "train_end_ms": dataset.timestamps[split.train_end - 1],
                "test_start_ms": dataset.timestamps[split.test_start],
                "test_end_ms": dataset.timestamps[split.test_end - 1],
                "train_rows": len(x_train),
                "test_rows": len(x_test),
                "baseline": baseline,
                "scores": score_payload,
            }
        )

    if not fold_reports:
        raise ValueError("no valid walk-forward folds with both target classes")

    baseline_summary = {
        "folds": len(baseline_rows),
        "mean_accuracy": float(np.mean([x["accuracy"] for x in baseline_rows])),
        "mean_brier_score": float(np.mean([x["brier_score"] for x in baseline_rows])),
    }

    summary = {}
    for family, scores in aggregate.items():
        mean_accuracy = float(np.mean([s["accuracy"] for s in scores]))
        mean_brier = float(np.mean([s["brier_score"] for s in scores]))
        brier_wins = sum(
            1
            for score, baseline in zip(scores, baseline_rows, strict=True)
            if score["brier_score"] < baseline["brier_score"]
        )
        summary[family] = {
            "folds": len(scores),
            "mean_accuracy": mean_accuracy,
            "mean_brier_score": mean_brier,
            "accuracy_lift_vs_baseline": (
                mean_accuracy - baseline_summary["mean_accuracy"]
            ),
            "brier_improvement_vs_baseline": (
                (baseline_summary["mean_brier_score"] - mean_brier)
                / baseline_summary["mean_brier_score"]
                if baseline_summary["mean_brier_score"] > 0
                else 0.0
            ),
            "brier_wins_vs_baseline": brier_wins,
        }

    selected = min(
        summary,
        key=lambda family: (
            summary[family]["mean_brier_score"],
            -summary[family]["mean_accuracy"],
        ),
    )
    selected_summary = summary[selected]
    evidence_gate_pass = (
        len(fold_reports) >= 3
        and selected_summary["brier_improvement_vs_baseline"] >= 0.02
        and selected_summary["brier_wins_vs_baseline"] >= max(
            2,
            int(np.ceil(len(fold_reports) * 0.67)),
        )
    )
    return {
        "selected_model_family": selected,
        "baseline_summary": baseline_summary,
        "model_summary": summary,
        "evidence_gate": {
            "passed": evidence_gate_pass,
            "minimum_folds": 3,
            "minimum_mean_brier_improvement": 0.02,
            "minimum_brier_fold_win_fraction": 0.67,
        },
        "folds": fold_reports,
    }


def save_native_model(model, model_family: str, output_dir: Path) -> tuple[Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    if model_family == "lightgbm":
        path = output_dir / "model.txt"
        model.booster_.save_model(str(path))
    elif model_family == "xgboost":
        path = output_dir / "model.json"
        model.save_model(str(path))
    elif model_family == "catboost":
        path = output_dir / "model.cbm"
        model.save_model(str(path))
    else:
        raise ValueError(f"unsupported model family: {model_family}")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


async def train_symbol(
    *,
    symbol: str,
    days: int,
    output_dir: Path,
    source: str = "archive",
    horizon: int = 4,
    positive_return_threshold: float = 0.0015,
) -> dict:
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise ValueError("V1 trains BTCUSDT and ETHUSDT only")
    if not 7 <= days <= 365:
        raise ValueError("days must be between 7 and 365")
    if source not in {"archive", "rest"}:
        raise ValueError("source must be archive or rest")

    settings = Settings()
    end = datetime.now(UTC)

    if source == "archive":
        if days > 90:
            raise ValueError("archive source supports at most 90 days per run")
        candles = await BybitPublicArchiveClient().historical_15m(
            symbol,
            days=days,
        )
    else:
        if days < 14:
            raise ValueError("REST training requires at least 14 days")
        client = BybitPublicClient(settings)
        end_ms = int(end.timestamp() * 1000)
        start_ms = int((end - timedelta(days=days)).timestamp() * 1000)
        candles = await client.historical_klines(
            symbol,
            "15m",
            start_ms=start_ms,
            end_ms=end_ms,
        )
        interval_ms = 15 * 60 * 1000
        last_closed_start = (end_ms // interval_ms) * interval_ms - interval_ms
        candles = [
            candle
            for candle in candles
            if int(candle["start_ms"]) <= last_closed_start
        ]

    dataset = build_training_dataset(
        candles,
        horizon=horizon,
        positive_return_threshold=positive_return_threshold,
    )
    evaluation = evaluate_walk_forward(dataset)
    selected = evaluation["selected_model_family"]

    final_model = make_binary_model(selected)
    final_model.fit(dataset.x, dataset.y)

    model_path, artifact_sha256 = save_native_model(
        final_model,
        selected,
        output_dir,
    )

    report = {
        "schema_version": 2,
        "symbol": symbol,
        "timeframe": "15m",
        "data_source": f"bybit_public_{source}",
        "generated_at": datetime.now(UTC).isoformat(),
        "history_start_ms": int(candles[0]["start_ms"]),
        "history_end_ms": int(candles[-1]["start_ms"]),
        "candle_count": len(candles),
        "dataset_rows": len(dataset.x),
        "positive_rate": float(dataset.y.mean()),
        "horizon_candles": horizon,
        "positive_return_threshold": positive_return_threshold,
        "feature_columns": ML_FEATURE_COLUMNS,
        "artifact_filename": model_path.name,
        "artifact_sha256": artifact_sha256,
        **evaluation,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train TriConsensus V1 ML evidence model")
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--source", choices=["archive", "rest"], default="archive")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    report = asyncio.run(
        train_symbol(
            symbol=args.symbol,
            days=args.days,
            output_dir=Path(args.output_dir),
            source=args.source,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
