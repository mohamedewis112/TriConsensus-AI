from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, date, datetime
import json
from pathlib import Path

import math

import numpy as np
import pandas as pd

from .kline_archive import BybitKlineArchiveClient, archive_coverage_stats
from .features import _adx, _atr, _rsi
from .regimes import (
    classify_regime,
    chronological_fold_ids,
    fit_regime_thresholds,
    summarize_regime,
)
from .training_v3 import trade_path_label


def build_regime_feature_frame(candles: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(candles).copy()
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
    frame["start_ms"] = pd.to_numeric(frame["start_ms"], errors="raise").astype("int64")

    frame["ret_1"] = frame["close"].pct_change()
    frame["ema_12"] = frame["close"].ewm(span=12, adjust=False).mean()
    frame["ema_20"] = frame["close"].ewm(span=20, adjust=False).mean()
    frame["ema_26"] = frame["close"].ewm(span=26, adjust=False).mean()
    frame["ema_50"] = frame["close"].ewm(span=50, adjust=False).mean()
    frame["rsi_14"] = _rsi(frame["close"], 14)
    frame["atr_14"] = _atr(frame, 14)
    frame["adx_14"] = _adx(frame, 14)
    frame["roc_10"] = frame["close"].pct_change(10)
    frame["realized_vol_20"] = frame["ret_1"].rolling(20).std() * math.sqrt(20)
    frame["macd"] = frame["ema_12"] - frame["ema_26"]
    frame["macd_signal"] = frame["macd"].ewm(span=9, adjust=False).mean()
    frame["macd_hist"] = frame["macd"] - frame["macd_signal"]
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    cumulative_volume = frame["volume"].cumsum().replace(0, np.nan)
    frame["vwap"] = (typical * frame["volume"]).cumsum() / cumulative_volume
    frame["support_20"] = frame["low"].rolling(20).min().shift(1)
    frame["resistance_20"] = frame["high"].rolling(20).max().shift(1)
    frame["volume_z_20"] = (
        (frame["volume"] - frame["volume"].rolling(20).mean())
        / frame["volume"].rolling(20).std().replace(0, np.nan)
    )
    frame["atr_normalized_move"] = (
        frame["close"].diff() / frame["atr_14"].replace(0, np.nan)
    )
    return frame


def _feature_dict(row: pd.Series) -> dict:
    required = {
        "close": row["close"],
        "ema_20": row["ema_20"],
        "ema_50": row["ema_50"],
        "atr_14": row["atr_14"],
        "adx_14": row["adx_14"],
        "realized_vol_20": row["realized_vol_20"],
        "macd_hist": row["macd_hist"],
        "roc_10": row["roc_10"],
    }
    if not all(np.isfinite(float(value)) for value in required.values()):
        raise ValueError("non-finite regime feature row")
    return {key: float(value) for key, value in required.items()}


def contiguous_evaluation_mask(
    candles: list[dict],
    *,
    lookback: int,
    horizon: int,
    interval_ms: int = 15 * 60 * 1000,
) -> np.ndarray:
    if lookback <= 0 or horizon <= 0:
        raise ValueError("lookback and horizon must be positive")
    starts = np.asarray([int(row["start_ms"]) for row in candles], dtype=np.int64)
    n = len(starts)
    mask = np.zeros(n, dtype=bool)
    if n < lookback + horizon:
        return mask

    gap_at = np.zeros(n, dtype=np.int64)
    if n > 1:
        gap_at[1:] = (np.diff(starts) != interval_ms).astype(np.int64)
    prefix = np.cumsum(gap_at)

    for i in range(lookback - 1, n - horizon):
        left = i - lookback + 1
        right = i + horizon
        gaps_inside = int(prefix[right] - prefix[left])
        mask[i] = gaps_inside == 0
    return mask


def build_regime_observations(
    candles: list[dict],
    *,
    lookback: int = 120,
    horizon: int = 12,
    calibration_rows: int = 480,
    stride: int = 4,
    round_trip_cost: float = 0.0015,
) -> tuple[dict, list[dict]]:
    if stride <= 0:
        raise ValueError("stride must be positive")
    if len(candles) < lookback + horizon + calibration_rows:
        raise ValueError("not enough candles for regime research")

    frame = build_regime_feature_frame(candles)
    contiguous = contiguous_evaluation_mask(
        candles,
        lookback=lookback,
        horizon=horizon,
    )
    usable_start = max(lookback - 1, 60)
    usable_end = len(candles) - horizon
    indices = [
        i
        for i in range(usable_start, usable_end)
        if contiguous[i]
        and np.isfinite(float(frame.iloc[i]["realized_vol_20"]))
    ]
    if len(indices) < calibration_rows + 100:
        raise ValueError("not enough finite regime feature rows")

    calibration_indices = indices[:calibration_rows]
    calibration = [
        float(frame.iloc[i]["realized_vol_20"])
        for i in calibration_indices
    ]
    thresholds = fit_regime_thresholds(calibration)

    observations: list[dict] = []
    evaluation_indices = indices[calibration_rows::stride]
    for candle_index in evaluation_indices:
        features = _feature_dict(frame.iloc[candle_index])
        regime = classify_regime(features, thresholds)
        for side_name, side in (("LONG", 1), ("SHORT", -1)):
            _, net_return = trade_path_label(
                candles,
                index=candle_index,
                side=side,
                atr=float(features["atr_14"]),
                horizon=horizon,
                round_trip_cost=round_trip_cost,
            )
            observations.append(
                {
                    "timestamp_ms": int(candles[candle_index]["start_ms"]),
                    "regime": regime.key,
                    "trend": regime.trend,
                    "volatility": regime.volatility,
                    "momentum": regime.momentum,
                    "side": side_name,
                    "net_return": float(net_return),
                    "trend_strength_atr": regime.trend_strength_atr,
                    "realized_volatility": regime.realized_volatility,
                    "adx": regime.adx,
                }
            )

    return asdict(thresholds), observations


def _summarize_groups(
    observations: list[dict],
    *,
    fold_by_timestamp: dict[int, int],
    key_builder,
) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in observations:
        grouped[(key_builder(row), str(row["side"]))].append(row)

    summaries = []
    for (regime, side), rows in sorted(grouped.items()):
        values = [float(row["net_return"]) for row in rows]
        fold_ids = [fold_by_timestamp[int(row["timestamp_ms"])] for row in rows]
        summaries.append(asdict(summarize_regime(regime, side, values, fold_ids)))
    return sorted(
        summaries,
        key=lambda row: (
            row["qualified"],
            row["bootstrap_lcb_95"],
            row["mean_net_return"],
            row["trades"],
        ),
        reverse=True,
    )


def analyze_regimes(observations: list[dict], *, folds: int = 4) -> dict:
    if not observations:
        raise ValueError("no regime observations")

    timestamps = sorted({int(row["timestamp_ms"]) for row in observations})
    fold_by_timestamp = dict(
        zip(
            timestamps,
            chronological_fold_ids(len(timestamps), folds),
            strict=True,
        )
    )

    full = _summarize_groups(
        observations,
        fold_by_timestamp=fold_by_timestamp,
        key_builder=lambda row: str(row["regime"]),
    )
    trend_volatility = _summarize_groups(
        observations,
        fold_by_timestamp=fold_by_timestamp,
        key_builder=lambda row: f'{row["trend"]}|{row["volatility"]}',
    )
    trend_only = _summarize_groups(
        observations,
        fold_by_timestamp=fold_by_timestamp,
        key_builder=lambda row: str(row["trend"]),
    )
    volatility_only = _summarize_groups(
        observations,
        fold_by_timestamp=fold_by_timestamp,
        key_builder=lambda row: str(row["volatility"]),
    )

    qualified = [row for row in full if row["qualified"]]
    return {
        "regime_count": len({row["regime"] for row in full}),
        "qualified_count": len(qualified),
        "qualified": qualified,
        "ranked": full,
        "hierarchy": {
            "trend_volatility": trend_volatility,
            "trend_only": trend_only,
            "volatility_only": volatility_only,
        },
    }


async def run_regime_research(
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
    thresholds, observations = build_regime_observations(candles)
    analysis = analyze_regimes(observations)
    data_quality = archive_coverage_stats(candles, requested_days=days)

    report = {
        "experiment": "V4-REGIME-DISCOVERY",
        "schema_version": 1,
        "symbol": symbol,
        "timeframe": "15m",
        "archive_end_day": end_day.isoformat(),
        "history_days": days,
        "history_start_ms": int(candles[0]["start_ms"]),
        "history_end_ms": int(candles[-1]["start_ms"]),
        "candle_count": len(candles),
        "observation_count": len(observations),
        "data_quality": data_quality,
        "gap_safe_windows_only": True,
        "round_trip_cost": 0.0015,
        "barrier_horizon": 12,
        "stop_atr": 1.5,
        "risk_reward": 1.5,
        "volatility_thresholds_calibrated_on_first_rows_only": thresholds,
        "generated_at": datetime.now(UTC).isoformat(),
        **analysis,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "regime-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    policy = {
        "schema_version": 1,
        "symbol": symbol,
        "source_report": "regime-report.json",
        "generated_at": report["generated_at"],
        "default_action": "NO_TRADE",
        "qualified_pairs": [
            {
                "regime": row["regime"],
                "side": row["side"],
                "trades": row["trades"],
                "mean_net_return": row["mean_net_return"],
                "profit_factor": row["profit_factor"],
                "bootstrap_lcb_95": row["bootstrap_lcb_95"],
                "profitable_folds": row["profitable_folds"],
                "folds": row["folds"],
            }
            for row in report["qualified"]
        ],
    }
    (output_dir / "qualified-policy.json").write_text(
        json.dumps(policy, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="TriConsensus V4 regime discovery")
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = asyncio.run(
        run_regime_research(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
