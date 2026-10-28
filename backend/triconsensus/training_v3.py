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
from .meta_label import make_meta_model, score_meta_model
from .walk_forward import walk_forward_splits


V3_FEATURE_COLUMNS = [
    "side",
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
]


@dataclass(frozen=True)
class MetaDataset:
    x: pd.DataFrame
    y: pd.Series
    net_returns: pd.Series
    timestamps: list[int]
    sides: list[int]


def _frame(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candles)[["open", "high", "low", "close", "volume"]]


def deterministic_candidate(features: dict) -> int:
    """
    Frozen V3 primary setup generator.

    +1 long, -1 short, 0 abstain. This is deliberately simple and symmetric:
    trend + VWAP location + momentum agreement, with a weak ADX quality floor.
    """
    close = float(features["close"])
    ema20 = float(features["ema_20"])
    ema50 = float(features["ema_50"])
    vwap = float(features["vwap"])
    rsi = float(features["rsi_14"])
    macd_hist = float(features["macd_hist"])
    adx = float(features["adx_14"])

    if adx < 15:
        return 0
    if ema20 > ema50 and close > vwap and rsi > 50 and macd_hist > 0:
        return 1
    if ema20 < ema50 and close < vwap and rsi < 50 and macd_hist < 0:
        return -1
    return 0


def trade_path_label(
    candles: list[dict],
    *,
    index: int,
    side: int,
    atr: float,
    horizon: int,
    min_rr: float = 1.5,
    stop_atr: float = 1.5,
    round_trip_cost: float = 0.0015,
) -> tuple[int, float]:
    if side not in {-1, 1}:
        raise ValueError("side must be -1 or +1")
    if atr <= 0 or horizon <= 0:
        raise ValueError("invalid ATR/horizon")

    entry = float(candles[index]["close"])
    stop_distance = atr * stop_atr
    target_distance = stop_distance * min_rr

    if side == 1:
        stop = entry - stop_distance
        target = entry + target_distance
    else:
        stop = entry + stop_distance
        target = entry - target_distance

    for future in candles[index + 1 : index + horizon + 1]:
        high = float(future["high"])
        low = float(future["low"])
        if side == 1:
            hit_stop = low <= stop
            hit_target = high >= target
        else:
            hit_stop = high >= stop
            hit_target = low <= target

        # If both barriers occur inside the same 15m candle, intrabar order is
        # unknown. Score it conservatively as the stop being hit first.
        if hit_stop:
            gross = -(stop_distance / entry)
            return 0, gross - round_trip_cost
        if hit_target:
            gross = target_distance / entry
            return 1, gross - round_trip_cost

    exit_price = float(candles[index + horizon]["close"])
    gross = side * ((exit_price / entry) - 1.0)
    net = gross - round_trip_cost
    return int(net > 0), net


def v3_vector(window: list[dict], features: dict, side: int) -> dict[str, float]:
    closes = [float(row["close"]) for row in window]
    latest = window[-1]
    close = closes[-1]
    open_ = float(latest["open"])
    high = float(latest["high"])
    low = float(latest["low"])
    atr = float(features["atr_14"])
    support = float(features["support_20"])
    resistance = float(features["resistance_20"])
    recent20 = closes[-20:]
    lo20 = min(recent20)
    hi20 = max(recent20)
    close_location = 0.5 if hi20 == lo20 else (close - lo20) / (hi20 - lo20)

    ts = datetime.fromtimestamp(int(latest["start_ms"]) / 1000, tz=UTC)
    angle = 2 * math.pi * ((ts.hour * 60 + ts.minute) / 1440)

    vector = {
        "side": float(side),
        "ema20_distance": float(features["ema_20"]) / close - 1,
        "ema50_distance": float(features["ema_50"]) / close - 1,
        "ema_gap_atr": (
            (float(features["ema_20"]) - float(features["ema_50"])) / atr
            if atr > 0
            else 0.0
        ),
        "rsi_14": float(features["rsi_14"]) / 100,
        "macd_normalized": float(features["macd"]) / close,
        "macd_hist_normalized": float(features["macd_hist"]) / close,
        "atr_normalized": atr / close,
        "adx_14": float(features["adx_14"]) / 100,
        "vwap_distance": float(features["vwap"]) / close - 1,
        "stoch_k": float(features["stoch_k"]) / 100,
        "stoch_d": float(features["stoch_d"]) / 100,
        "roc_10": float(features["roc_10"]),
        "realized_vol_20": float(features["realized_vol_20"]),
        "volume_z_20": float(features["volume_z_20"]),
        "support_distance": close / support - 1,
        "resistance_distance": resistance / close - 1,
        "atr_normalized_move": float(features["atr_normalized_move"]),
        "return_1": closes[-1] / closes[-2] - 1,
        "return_4": closes[-1] / closes[-5] - 1,
        "return_16": closes[-1] / closes[-17] - 1,
        "range_1": (high - low) / close,
        "body_1": (close - open_) / open_,
        "close_location_20": close_location,
        "hour_sin": math.sin(angle),
        "hour_cos": math.cos(angle),
    }
    if list(vector) != V3_FEATURE_COLUMNS:
        raise ValueError("V3 feature schema changed unexpectedly")
    if not all(np.isfinite(value) for value in vector.values()):
        raise ValueError("V3 vector contains non-finite values")
    return vector


def build_v3_dataset(
    candles: list[dict],
    *,
    horizon: int = 12,
    lookback: int = 120,
    round_trip_cost: float = 0.0015,
) -> MetaDataset:
    if len(candles) < lookback + horizon + 400:
        raise ValueError("not enough candles for V3")

    rows: list[dict[str, float]] = []
    labels: list[int] = []
    returns: list[float] = []
    timestamps: list[int] = []
    sides: list[int] = []

    for i in range(lookback - 1, len(candles) - horizon):
        window = candles[i - lookback + 1 : i + 1]
        features = build_features(_frame(window))
        side = deterministic_candidate(features)
        if side == 0:
            continue

        label, net_return = trade_path_label(
            candles,
            index=i,
            side=side,
            atr=float(features["atr_14"]),
            horizon=horizon,
            round_trip_cost=round_trip_cost,
        )
        rows.append(v3_vector(window, features, side))
        labels.append(label)
        returns.append(net_return)
        timestamps.append(int(candles[i]["start_ms"]))
        sides.append(side)

    if len(rows) < 300:
        raise ValueError(f"too few V3 candidates: {len(rows)}")

    x = pd.DataFrame(rows, columns=V3_FEATURE_COLUMNS)
    y = pd.Series(labels, name="trade_success", dtype="int64")
    net = pd.Series(returns, name="net_return", dtype="float64")
    if y.nunique() < 2:
        raise ValueError("V3 labels contain only one class")
    return MetaDataset(x=x, y=y, net_returns=net, timestamps=timestamps, sides=sides)


def _evaluate_family(
    family: str,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_test: pd.DataFrame,
    y_test: pd.Series,
    test_net: pd.Series,
) -> dict:
    model = make_meta_model(family)
    model.fit(x_train, y_train)
    score = score_meta_model(
        model_family=family,
        train_probability=model.predict_proba(x_train)[:, 1],
        test_probability=model.predict_proba(x_test)[:, 1],
        y_train=y_train,
        y_test=y_test,
        test_net_returns=test_net,
        selection_fraction=0.30,
    )
    return asdict(score)


def evaluate_v3(dataset: MetaDataset, *, horizon: int) -> dict:
    n = len(dataset.x)
    holdout_size = max(100, int(n * 0.20))
    holdout_start = n - holdout_size
    selection_n = holdout_start

    train_size = max(200, int(selection_n * 0.50))
    test_size = max(60, int(selection_n * 0.10))
    splits = walk_forward_splits(
        selection_n,
        train_size=train_size,
        test_size=test_size,
        step_size=test_size,
        embargo_size=max(1, int(math.ceil(horizon / 2))),
    )
    if len(splits) < 3:
        raise ValueError("V3 requires at least three selection folds")

    families = ("lightgbm", "xgboost", "catboost")
    per_family: dict[str, list[dict]] = defaultdict(list)
    folds = []

    for fold_number, split in enumerate(splits, start=1):
        x_train = dataset.x.iloc[split.train_start : split.train_end]
        y_train = dataset.y.iloc[split.train_start : split.train_end]
        x_test = dataset.x.iloc[split.test_start : split.test_end]
        y_test = dataset.y.iloc[split.test_start : split.test_end]
        test_net = dataset.net_returns.iloc[split.test_start : split.test_end]
        if y_train.nunique() < 2 or y_test.nunique() < 2:
            continue

        scores = [
            _evaluate_family(family, x_train, y_train, x_test, y_test, test_net)
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
                "candidate_rows": len(x_test),
                "scores": scores,
            }
        )

    if len(folds) < 3:
        raise ValueError("V3 produced fewer than three valid folds")

    summary = {}
    for family, scores in per_family.items():
        profitable = sum(1 for s in scores if s["mean_net_return"] > 0)
        beat_baseline = sum(1 for s in scores if s["excess_net_return"] > 0)
        summary[family] = {
            "folds": len(scores),
            "mean_brier_improvement": float(
                np.mean([s["brier_improvement"] for s in scores])
            ),
            "mean_net_return": float(np.mean([s["mean_net_return"] for s in scores])),
            "mean_excess_net_return": float(
                np.mean([s["excess_net_return"] for s in scores])
            ),
            "mean_hit_rate": float(np.mean([s["hit_rate"] for s in scores])),
            "profitable_folds": profitable,
            "beat_baseline_folds": beat_baseline,
        }

    minimum_stable = max(2, int(math.ceil(len(folds) * 0.67)))
    eligible = [
        family
        for family in families
        if summary[family]["profitable_folds"] >= minimum_stable
        and summary[family]["beat_baseline_folds"] >= minimum_stable
        and summary[family]["mean_brier_improvement"] > 0
    ]
    selection_pool = eligible or list(families)
    selected = max(
        selection_pool,
        key=lambda family: (
            summary[family]["mean_excess_net_return"],
            summary[family]["mean_brier_improvement"],
        ),
    )

    final_train_end = holdout_start - max(1, int(math.ceil(horizon / 2)))
    x_train = dataset.x.iloc[:final_train_end]
    y_train = dataset.y.iloc[:final_train_end]
    x_holdout = dataset.x.iloc[holdout_start:]
    y_holdout = dataset.y.iloc[holdout_start:]
    net_holdout = dataset.net_returns.iloc[holdout_start:]

    model = make_meta_model(selected)
    model.fit(x_train, y_train)
    holdout = asdict(
        score_meta_model(
            model_family=selected,
            train_probability=model.predict_proba(x_train)[:, 1],
            test_probability=model.predict_proba(x_holdout)[:, 1],
            y_train=y_train,
            y_test=y_holdout,
            test_net_returns=net_holdout,
            selection_fraction=0.30,
        )
    )

    gate = (
        selected in eligible
        and holdout["trade_count"] >= 20
        and holdout["mean_net_return"] >= 0.0005
        and holdout["excess_net_return"] > 0
        and holdout["brier_improvement"] > 0
    )
    return {
        "selected_model_family": selected,
        "selection_summary": summary,
        "selection_folds": folds,
        "holdout": {
            "rows": len(x_holdout),
            "start_ms": dataset.timestamps[holdout_start],
            **holdout,
        },
        "evidence_gate": {
            "passed": gate,
            "minimum_stable_folds": minimum_stable,
            "minimum_holdout_trades": 20,
            "minimum_holdout_mean_net_return": 0.0005,
            "requires_positive_excess_vs_baseline": True,
            "requires_positive_brier_improvement": True,
        },
        "qualification_model": model,
    }


async def train_v3(
    *,
    symbol: str,
    days: int,
    end_day: date,
    output_dir: Path,
) -> dict:
    candles = await BybitPublicArchiveClient().historical_15m(
        symbol,
        days=days,
        end_day=end_day,
    )
    round_trip_cost = 0.0015
    dataset = build_v3_dataset(
        candles,
        horizon=12,
        round_trip_cost=round_trip_cost,
    )
    evaluation = evaluate_v3(dataset, horizon=12)
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
        "experiment": "ML-V3-META-LABEL",
        "schema_version": 1,
        "symbol": symbol,
        "timeframe": "15m",
        "archive_end_day": end_day.isoformat(),
        "history_days": days,
        "history_start_ms": int(candles[0]["start_ms"]),
        "history_end_ms": int(candles[-1]["start_ms"]),
        "candle_count": len(candles),
        "candidate_count": len(dataset.x),
        "candidate_success_rate": float(dataset.y.mean()),
        "long_candidates": int(sum(1 for s in dataset.sides if s == 1)),
        "short_candidates": int(sum(1 for s in dataset.sides if s == -1)),
        "target": "meta-label: deterministic setup TP/positive-exit before failure",
        "barrier_horizon": 12,
        "stop_atr": 1.5,
        "risk_reward": 1.5,
        "round_trip_cost": round_trip_cost,
        "feature_columns": V3_FEATURE_COLUMNS,
        "artifact_filename": model_path.name,
        "artifact_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "generated_at": datetime.now(UTC).isoformat(),
        **evaluation,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="TriConsensus ML V3 meta-label research")
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = asyncio.run(
        train_v3(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
