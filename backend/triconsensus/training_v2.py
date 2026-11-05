from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .archive_data import BybitPublicArchiveClient
from .features import build_features
from .ml_regression import (
    evaluate_predictions,
    make_regression_model,
)
from .walk_forward import walk_forward_splits


V2_FEATURE_COLUMNS = [
    "ema20_distance",
    "ema50_distance",
    "ema_gap_atr",
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
    "return_1",
    "return_4",
    "return_16",
    "range_1",
    "body_1",
    "close_location_20",
    "hour_sin",
    "hour_cos",
    "h1_ema20_distance",
    "h1_ema50_distance",
    "h1_rsi_14",
    "h1_adx_14",
    "h1_roc_10",
    "h1_realized_vol_20",
    "h1_atr_normalized",
]


@dataclass(frozen=True)
class RegressionDataset:
    x: pd.DataFrame
    y: pd.Series
    timestamps: list[int]


def _frame(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candles)[["open", "high", "low", "close", "volume"]]


def _hourly_frame(candles: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(candles)
    frame["hour_bucket"] = (frame["start_ms"].astype("int64") // 3_600_000)
    grouped = frame.groupby("hour_bucket", sort=True)
    return pd.DataFrame(
        {
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(),
        }
    ).reset_index(drop=True)


def v2_vector(window: list[dict]) -> dict[str, float]:
    if len(window) < 240:
        raise ValueError("V2 requires 240 x 15m candles of lookback")

    f15 = build_features(_frame(window))
    h1 = _hourly_frame(window)
    if len(h1) < 60:
        raise ValueError("V2 requires at least 60 complete hourly buckets")
    f1h = build_features(h1)

    closes = [float(row["close"]) for row in window]
    latest = window[-1]
    close = closes[-1]
    high = float(latest["high"])
    low = float(latest["low"])
    open_ = float(latest["open"])
    support = float(f15["support_20"])
    resistance = float(f15["resistance_20"])
    atr = float(f15["atr_14"])

    recent20 = closes[-20:]
    lo20 = min(recent20)
    hi20 = max(recent20)
    close_location = 0.5 if hi20 == lo20 else (close - lo20) / (hi20 - lo20)

    ts = datetime.fromtimestamp(int(latest["start_ms"]) / 1000, tz=UTC)
    angle = 2 * math.pi * ((ts.hour * 60 + ts.minute) / 1440)

    vector = {
        "ema20_distance": float(f15["ema_20"]) / close - 1,
        "ema50_distance": float(f15["ema_50"]) / close - 1,
        "ema_gap_atr": (
            (float(f15["ema_20"]) - float(f15["ema_50"])) / atr if atr > 0 else 0
        ),
        "rsi_14": float(f15["rsi_14"]) / 100,
        "macd_normalized": float(f15["macd"]) / close,
        "macd_hist_normalized": float(f15["macd_hist"]) / close,
        "atr_normalized": atr / close,
        "adx_14": float(f15["adx_14"]) / 100,
        "vwap_distance": float(f15["vwap"]) / close - 1,
        "stoch_k": float(f15["stoch_k"]) / 100,
        "stoch_d": float(f15["stoch_d"]) / 100,
        "roc_10": float(f15["roc_10"]),
        "realized_vol_20": float(f15["realized_vol_20"]),
        "volume_z_20": float(f15["volume_z_20"]),
        "support_distance": close / support - 1,
        "resistance_distance": resistance / close - 1,
        "atr_normalized_move": float(f15["atr_normalized_move"]),
        "return_1": closes[-1] / closes[-2] - 1,
        "return_4": closes[-1] / closes[-5] - 1,
        "return_16": closes[-1] / closes[-17] - 1,
        "range_1": (high - low) / close,
        "body_1": (close - open_) / open_,
        "close_location_20": close_location,
        "hour_sin": math.sin(angle),
        "hour_cos": math.cos(angle),
        "h1_ema20_distance": float(f1h["ema_20"]) / float(f1h["close"]) - 1,
        "h1_ema50_distance": float(f1h["ema_50"]) / float(f1h["close"]) - 1,
        "h1_rsi_14": float(f1h["rsi_14"]) / 100,
        "h1_adx_14": float(f1h["adx_14"]) / 100,
        "h1_roc_10": float(f1h["roc_10"]),
        "h1_realized_vol_20": float(f1h["realized_vol_20"]),
        "h1_atr_normalized": float(f1h["atr_14"]) / float(f1h["close"]),
    }
    if list(vector) != V2_FEATURE_COLUMNS:
        raise ValueError("V2 feature schema changed unexpectedly")
    if not all(np.isfinite(value) for value in vector.values()):
        raise ValueError("V2 vector contains non-finite values")
    return vector


def build_v2_dataset(
    candles: list[dict],
    *,
    horizon: int = 4,
    lookback: int = 240,
) -> RegressionDataset:
    if len(candles) < lookback + horizon + 300:
        raise ValueError("not enough candles for V2")

    rows: list[dict[str, float]] = []
    targets: list[float] = []
    timestamps: list[int] = []
    for i in range(lookback - 1, len(candles) - horizon):
        window = candles[i - lookback + 1 : i + 1]
        current = float(candles[i]["close"])
        future = float(candles[i + horizon]["close"])
        rows.append(v2_vector(window))
        targets.append((future / current) - 1)
        timestamps.append(int(candles[i]["start_ms"]))

    x = pd.DataFrame(rows, columns=V2_FEATURE_COLUMNS)
    y = pd.Series(targets, name="forward_return", dtype="float64")
    if x.isna().any().any() or y.isna().any():
        raise ValueError("V2 dataset contains NaN")
    return RegressionDataset(x=x, y=y, timestamps=timestamps)


def _score_family(
    family: str,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_test: pd.DataFrame,
    y_test: pd.Series,
    *,
    round_trip_cost: float,
) -> dict:
    model = make_regression_model(family)
    model.fit(x_train, y_train)
    train_prediction = model.predict(x_train)
    test_prediction = model.predict(x_test)
    score = evaluate_predictions(
        model_family=family,
        train_prediction=train_prediction,
        test_prediction=test_prediction,
        test_return=y_test,
        round_trip_cost=round_trip_cost,
    )
    baseline_prediction = np.full(len(y_test), float(y_train.mean()))
    baseline_mse = float(np.mean((baseline_prediction - y_test.to_numpy()) ** 2))
    payload = asdict(score)
    payload["baseline_mse"] = baseline_mse
    payload["mse_improvement_vs_baseline"] = (
        (baseline_mse - score.mse) / baseline_mse if baseline_mse > 0 else 0.0
    )
    return payload


def evaluate_v2(
    dataset: RegressionDataset,
    *,
    horizon: int,
    round_trip_cost: float,
) -> dict:
    n = len(dataset.x)
    holdout_size = max(200, int(n * 0.20))
    holdout_start = n - holdout_size
    selection_n = holdout_start

    train_size = max(400, int(selection_n * 0.50))
    test_size = max(100, int(selection_n * 0.10))
    splits = walk_forward_splits(
        selection_n,
        train_size=train_size,
        test_size=test_size,
        step_size=test_size,
        embargo_size=horizon,
    )
    if len(splits) < 3:
        raise ValueError("V2 requires at least three purged selection folds")

    families = ("lightgbm", "xgboost", "catboost")
    per_family: dict[str, list[dict]] = defaultdict(list)
    folds: list[dict] = []

    for fold_number, split in enumerate(splits, start=1):
        x_train = dataset.x.iloc[split.train_start : split.train_end]
        y_train = dataset.y.iloc[split.train_start : split.train_end]
        x_test = dataset.x.iloc[split.test_start : split.test_end]
        y_test = dataset.y.iloc[split.test_start : split.test_end]
        scores = [
            _score_family(
                family,
                x_train,
                y_train,
                x_test,
                y_test,
                round_trip_cost=round_trip_cost,
            )
            for family in families
        ]
        for score in scores:
            per_family[score["model_family"]].append(score)
        folds.append(
            {
                "fold": fold_number,
                "train_end_ms": dataset.timestamps[split.train_end - 1],
                "test_start_ms": dataset.timestamps[split.test_start],
                "test_end_ms": dataset.timestamps[split.test_end - 1],
                "embargo_rows": split.test_start - split.train_end,
                "scores": scores,
            }
        )

    summary: dict[str, dict] = {}
    for family, scores in per_family.items():
        profitable = sum(1 for score in scores if score["mean_net_edge"] > 0)
        summary[family] = {
            "folds": len(scores),
            "mean_mse": float(np.mean([s["mse"] for s in scores])),
            "mean_mse_improvement_vs_baseline": float(
                np.mean([s["mse_improvement_vs_baseline"] for s in scores])
            ),
            "mean_rank_ic": float(np.mean([s["rank_ic"] for s in scores])),
            "mean_net_edge": float(np.mean([s["mean_net_edge"] for s in scores])),
            "mean_hit_rate": float(np.mean([s["hit_rate"] for s in scores])),
            "profitable_folds": profitable,
        }

    eligible = [
        family
        for family in families
        if summary[family]["mean_rank_ic"] > 0
        and summary[family]["mean_net_edge"] > 0
    ]
    selection_pool = eligible or list(families)
    selected = min(
        selection_pool,
        key=lambda family: (
            summary[family]["mean_mse"],
            -summary[family]["mean_net_edge"],
        ),
    )

    final_train_end = holdout_start - horizon
    x_train = dataset.x.iloc[:final_train_end]
    y_train = dataset.y.iloc[:final_train_end]
    x_holdout = dataset.x.iloc[holdout_start:]
    y_holdout = dataset.y.iloc[holdout_start:]

    final_model = make_regression_model(selected)
    final_model.fit(x_train, y_train)
    holdout_score = asdict(
        evaluate_predictions(
            model_family=selected,
            train_prediction=final_model.predict(x_train),
            test_prediction=final_model.predict(x_holdout),
            test_return=y_holdout,
            round_trip_cost=round_trip_cost,
        )
    )
    baseline_prediction = np.full(len(y_holdout), float(y_train.mean()))
    baseline_mse = float(
        np.mean((baseline_prediction - y_holdout.to_numpy()) ** 2)
    )
    holdout_score["baseline_mse"] = baseline_mse
    holdout_score["mse_improvement_vs_baseline"] = (
        (baseline_mse - holdout_score["mse"]) / baseline_mse
        if baseline_mse > 0
        else 0.0
    )

    minimum_profitable_folds = max(2, int(math.ceil(len(splits) * 0.67)))
    gate = (
        summary[selected]["profitable_folds"] >= minimum_profitable_folds
        and holdout_score["trade_count"] >= 20
        and holdout_score["mean_net_edge"] >= 0.0005
        and holdout_score["rank_ic"] >= 0.03
    )
    return {
        "selected_model_family": selected,
        "selection_summary": summary,
        "selection_folds": folds,
        "holdout": {
            "start_ms": dataset.timestamps[holdout_start],
            "rows": len(x_holdout),
            **holdout_score,
        },
        "evidence_gate": {
            "passed": gate,
            "minimum_profitable_selection_folds": minimum_profitable_folds,
            "minimum_holdout_trades": 20,
            "minimum_holdout_mean_net_edge": 0.0005,
            "minimum_holdout_rank_ic": 0.03,
        },
        "qualification_model": final_model,
    }


async def train_v2(
    *,
    symbol: str,
    days: int,
    output_dir: Path,
    end_day: date | None = None,
) -> dict:
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise ValueError("unsupported V2 symbol")
    candles = await BybitPublicArchiveClient().historical_15m(
        symbol,
        days=days,
        end_day=end_day,
    )
    dataset = build_v2_dataset(candles)

    # Two taker fees + 2 bps adverse slippage on entry and exit.
    round_trip_cost = (2 * 0.00055) + (2 * 0.0002)
    evaluation = evaluate_v2(
        dataset,
        horizon=4,
        round_trip_cost=round_trip_cost,
    )
    model = evaluation.pop("qualification_model")
    output_dir.mkdir(parents=True, exist_ok=True)

    family = evaluation["selected_model_family"]
    if family == "lightgbm":
        model_path = output_dir / "model.txt"
        model.booster_.save_model(str(model_path))
    elif family == "xgboost":
        model_path = output_dir / "model.json"
        model.save_model(str(model_path))
    else:
        model_path = output_dir / "model.cbm"
        model.save_model(str(model_path))

    report = {
        "experiment": "ML-V2",
        "schema_version": 1,
        "symbol": symbol,
        "timeframe": "15m",
        "data_source": "bybit_public_archive",
        "generated_at": datetime.now(UTC).isoformat(),
        "history_days": days,
        "archive_end_day": end_day.isoformat() if end_day else "T-2",
        "history_start_ms": int(candles[0]["start_ms"]),
        "history_end_ms": int(candles[-1]["start_ms"]),
        "candle_count": len(candles),
        "dataset_rows": len(dataset.x),
        "target": "4-candle forward return regression",
        "round_trip_cost": round_trip_cost,
        "feature_columns": V2_FEATURE_COLUMNS,
        "artifact_filename": model_path.name,
        "artifact_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        **evaluation,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="TriConsensus ML V2 regression research")
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    end_day = date.fromisoformat(args.end_date) if args.end_date else None
    report = asyncio.run(
        train_v2(
            symbol=args.symbol,
            days=args.days,
            output_dir=Path(args.output_dir),
            end_day=end_day,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
