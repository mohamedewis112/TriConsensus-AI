from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import UTC, date, datetime
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .kline_archive import BybitKlineArchiveClient, archive_coverage_stats
from .regime_research import contiguous_evaluation_mask
from .regimes import classify_regime, fit_regime_thresholds
from .v7_signal_lab import (
    V7_HORIZON_BARS,
    V7_SIGNAL_LAB_VERSION,
    build_v7_price_frame,
    generate_price_candidates,
    meta_feature_vector,
    multi_horizon_outcome,
)
from .v7_validation import (
    purged_train_end_before_holdout,
    purged_walk_forward_splits,
)


EMBARGO_MS = 180 * 60 * 1000


def _profit_factor(values: list[float]) -> float | None:
    profit = sum(x for x in values if x > 0)
    loss = abs(sum(x for x in values if x < 0))
    return None if loss == 0 else profit / loss


def _max_drawdown(values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return float(worst)


def build_v7_observations(
    candles: list[dict],
    *,
    calibration_rows: int = 480,
    lookback: int = 120,
    stride: int = 1,
    round_trip_cost: float = 0.0015,
) -> tuple[dict, list[dict]]:
    if stride <= 0:
        raise ValueError("stride must be positive")
    if len(candles) < calibration_rows + lookback + 200:
        raise ValueError("not enough candles for V7 research")

    frame = build_v7_price_frame(candles)
    contiguous = contiguous_evaluation_mask(
        candles,
        lookback=lookback,
        horizon=max(V7_HORIZON_BARS.values()),
    )
    required = (
        "close", "ema_20", "ema_50", "atr_14", "adx_14",
        "realized_vol_20", "macd_hist", "roc_10", "rsi_14",
        "volume_z_20", "support_20", "resistance_20",
        "return_1", "return_4", "return_16", "ema_gap_atr",
        "bb_z_20", "stoch_k", "atr_normalized_move",
        "breakout_up_distance_atr", "breakout_down_distance_atr",
    )

    eligible = [
        i
        for i in range(max(lookback - 1, 60), len(candles) - 12)
        if contiguous[i]
        and all(np.isfinite(float(frame.iloc[i][column])) for column in required)
    ]
    if len(eligible) < calibration_rows + 100:
        raise ValueError("not enough contiguous finite V7 rows")

    calibration = [
        float(frame.iloc[i]["realized_vol_20"])
        for i in eligible[:calibration_rows]
    ]
    thresholds = fit_regime_thresholds(calibration)

    observations: list[dict] = []
    for index in eligible[calibration_rows::stride]:
        row = frame.iloc[index]
        features = {column: float(row[column]) for column in required}
        regime = classify_regime(features, thresholds)
        candidates = generate_price_candidates(features, regime)
        if not candidates:
            continue

        timestamp_ms = int(candles[index]["start_ms"])
        for candidate in candidates:
            outcome = multi_horizon_outcome(
                candles,
                index=index,
                side=candidate.side,
                round_trip_cost=round_trip_cost,
            )
            observations.append(
                {
                    "timestamp_ms": timestamp_ms,
                    "entry_index": index,
                    "species": candidate.species,
                    "side": candidate.side,
                    "confidence": candidate.confidence,
                    "reason": candidate.reason,
                    "regime": candidate.regime_key,
                    "crowding_confirmed": candidate.crowding_confirmed,
                    "net_return_30m": outcome.net_return_30m,
                    "net_return_60m": outcome.net_return_60m,
                    "net_return_180m": outcome.net_return_180m,
                    "mae_180m": outcome.mae_180m,
                    "mfe_180m": outcome.mfe_180m,
                    "meta_features": meta_feature_vector(
                        features,
                        candidate,
                        regime,
                        timestamp_ms=timestamp_ms,
                    ),
                }
            )

    return {
        "low_vol": thresholds.low_vol,
        "high_vol": thresholds.high_vol,
        "calibration_rows": calibration_rows,
    }, observations


def _non_overlapping(rows: list[dict], *, bars: int = 12) -> list[dict]:
    selected: list[dict] = []
    busy_until = -1
    for row in sorted(rows, key=lambda x: (x["entry_index"], x["timestamp_ms"])):
        entry = int(row["entry_index"])
        if entry <= busy_until:
            continue
        selected.append(row)
        busy_until = entry + bars
    return selected


def summarize_species(observations: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in observations:
        groups[(str(row["species"]), str(row["side"]))].append(row)

    summaries: list[dict] = []
    for (species, side), rows in sorted(groups.items()):
        selected = _non_overlapping(rows)
        result = {
            "species": species,
            "side": side,
            "candidates": len(rows),
            "non_overlapping_180m": len(selected),
            "mean_mae_180m": float(np.mean([r["mae_180m"] for r in selected]))
            if selected else 0.0,
            "mean_mfe_180m": float(np.mean([r["mfe_180m"] for r in selected]))
            if selected else 0.0,
        }
        for label in ("30m", "60m", "180m"):
            values = [float(r[f"net_return_{label}"]) for r in selected]
            result[label] = {
                "trades": len(values),
                "wins": sum(1 for x in values if x > 0),
                "hit_rate": (
                    sum(1 for x in values if x > 0) / len(values)
                    if values else 0.0
                ),
                "mean_net_return": float(np.mean(values)) if values else 0.0,
                "profit_factor": _profit_factor(values),
                "max_drawdown": _max_drawdown(values),
            }
        summaries.append(result)
    return summaries


def _meta_dataset(observations: list[dict]) -> tuple[pd.DataFrame, pd.Series, pd.Series, list[int]]:
    ordered = sorted(observations, key=lambda row: (row["timestamp_ms"], row["species"], row["side"]))
    if not ordered:
        raise ValueError("no V7 candidates")
    columns = list(ordered[0]["meta_features"])
    x = pd.DataFrame([row["meta_features"] for row in ordered], columns=columns)
    y = pd.Series(
        [int(float(row["net_return_180m"]) > 0) for row in ordered],
        dtype="int64",
    )
    returns = pd.Series(
        [float(row["net_return_180m"]) for row in ordered],
        dtype="float64",
    )
    timestamps = [int(row["timestamp_ms"]) for row in ordered]
    return x, y, returns, timestamps


def _selection_metrics(probability: np.ndarray, threshold: float, returns: pd.Series) -> dict:
    selected = probability >= threshold
    values = returns.to_numpy(dtype=float)[selected]
    return {
        "trade_count": int(selected.sum()),
        "hit_rate": float(np.mean(values > 0)) if len(values) else 0.0,
        "mean_net_return": float(np.mean(values)) if len(values) else 0.0,
        "profit_factor": _profit_factor(values.tolist()),
        "max_drawdown": _max_drawdown(values.tolist()),
    }


def evaluate_logistic_meta_label(observations: list[dict]) -> dict:
    """Interpretable baseline only. No hyperparameter search is allowed in V7.0."""
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import brier_score_loss
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import make_pipeline
    except ImportError as exc:
        raise RuntimeError("V7 meta-label research requires scikit-learn") from exc

    x, y, returns, timestamps = _meta_dataset(observations)
    if len(x) < 300 or y.nunique() < 2:
        return {
            "status": "insufficient_sample",
            "candidate_rows": len(x),
            "evidence_gate_passed": False,
        }

    holdout_start = int(len(x) * 0.80)
    final_train_end = purged_train_end_before_holdout(
        timestamps,
        holdout_start=holdout_start,
        embargo_ms=EMBARGO_MS,
    )
    selection_n = final_train_end
    train_size = max(160, int(selection_n * 0.50))
    test_size = max(40, int(selection_n * 0.10))
    splits = purged_walk_forward_splits(
        timestamps[:selection_n],
        train_size=train_size,
        test_size=test_size,
        step_size=test_size,
        embargo_ms=EMBARGO_MS,
    )

    fold_results: list[dict] = []
    for fold, split in enumerate(splits, start=1):
        x_train = x.iloc[split.train_start:split.train_end]
        y_train = y.iloc[split.train_start:split.train_end]
        x_test = x.iloc[split.test_start:split.test_end]
        y_test = y.iloc[split.test_start:split.test_end]
        test_returns = returns.iloc[split.test_start:split.test_end]
        if y_train.nunique() < 2 or y_test.nunique() < 2:
            continue

        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(C=1.0, max_iter=1000, class_weight="balanced", random_state=42),
        )
        model.fit(x_train, y_train)
        train_probability = model.predict_proba(x_train)[:, 1]
        test_probability = model.predict_proba(x_test)[:, 1]
        threshold = float(np.quantile(train_probability, 0.65))
        metrics = _selection_metrics(test_probability, threshold, test_returns)
        base_probability = float(y_train.mean())
        metrics.update(
            {
                "fold": fold,
                "threshold": threshold,
                "brier_score": float(brier_score_loss(y_test, test_probability)),
                "baseline_brier_score": float(
                    brier_score_loss(y_test, np.full(len(y_test), base_probability))
                ),
                "train_end_ms": timestamps[split.train_end - 1],
                "test_start_ms": timestamps[split.test_start],
                "purge_gap_ms": timestamps[split.test_start] - timestamps[split.train_end - 1],
            }
        )
        fold_results.append(metrics)

    if len(fold_results) < 3:
        return {
            "status": "insufficient_valid_folds",
            "candidate_rows": len(x),
            "valid_folds": len(fold_results),
            "evidence_gate_passed": False,
        }

    final_model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=1.0, max_iter=1000, class_weight="balanced", random_state=42),
    )
    final_model.fit(x.iloc[:final_train_end], y.iloc[:final_train_end])
    train_probability = final_model.predict_proba(x.iloc[:final_train_end])[:, 1]
    threshold = float(np.quantile(train_probability, 0.65))
    holdout_probability = final_model.predict_proba(x.iloc[holdout_start:])[:, 1]
    holdout = _selection_metrics(
        holdout_probability,
        threshold,
        returns.iloc[holdout_start:],
    )
    holdout["rows"] = len(x) - holdout_start
    holdout["start_ms"] = timestamps[holdout_start]
    holdout["threshold"] = threshold
    holdout["hit_rate_all_candidates"] = float(y.iloc[holdout_start:].mean())

    profitable_folds = sum(1 for row in fold_results if row["mean_net_return"] > 0)
    pf = holdout["profit_factor"]
    gate = (
        holdout["trade_count"] >= 30
        and holdout["hit_rate"] >= 0.52
        and holdout["mean_net_return"] >= 0.0003
        and pf is not None and pf >= 1.25
        and profitable_folds >= 3
    )
    return {
        "status": "evaluated",
        "model_family": "logistic_regression_fixed_v1",
        "candidate_rows": len(x),
        "selection_folds": fold_results,
        "profitable_selection_folds": profitable_folds,
        "holdout": holdout,
        "evidence_gate": {
            "passed": gate,
            "minimum_holdout_trades": 30,
            "minimum_holdout_hit_rate": 0.52,
            "minimum_holdout_mean_net_return": 0.0003,
            "minimum_holdout_profit_factor": 1.25,
            "minimum_profitable_selection_folds": 3,
            "embargo_minutes": 180,
        },
        "evidence_gate_passed": gate,
    }


async def run_v7_research(
    *,
    symbol: str,
    days: int,
    end_day: date,
    output_dir: Path,
) -> dict:
    candles = await BybitKlineArchiveClient().historical_15m(
        symbol,
        days=days,
        end_day=end_day,
    )
    thresholds, observations = build_v7_observations(candles)
    quality = archive_coverage_stats(candles, requested_days=days)
    if quality["coverage_ratio"] < 0.90:
        raise ValueError("V7 qualification requires >=90% archive coverage")

    species = summarize_species(observations)
    meta = evaluate_logistic_meta_label(observations)
    report = {
        "experiment": "V7-SIGNAL-LAB",
        "schema_version": 1,
        "signal_lab_version": V7_SIGNAL_LAB_VERSION,
        "symbol": symbol,
        "timeframe": "15m",
        "history_days": days,
        "archive_end_day": end_day.isoformat(),
        "history_start_ms": int(candles[0]["start_ms"]),
        "history_end_ms": int(candles[-1]["start_ms"]),
        "candle_count": len(candles),
        "candidate_count": len(observations),
        "round_trip_cost": 0.0015,
        "multi_horizon_minutes": [30, 60, 180],
        "maximum_label_horizon_minutes": 180,
        "purged_walk_forward": True,
        "embargo_minutes": 180,
        "untouched_holdout_fraction": 0.20,
        "hyperparameter_search": False,
        "qualification_scope": "development_only_until_future_preregistered_shadow_holdout",
        "live_trading_authority": False,
        "paper_trading_authority": False,
        "v51_protocol_changed": False,
        "microstructure_species_backtest_status": "not_backtested_without_historical_orderbook",
        "data_quality": quality,
        "volatility_thresholds": thresholds,
        "species": species,
        "meta_label": meta,
        "generated_at": datetime.now(UTC).isoformat(),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "v7-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="TriConsensus V7 Signal Lab research")
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = asyncio.run(
        run_v7_research(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
