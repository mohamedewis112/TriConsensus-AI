from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, date, datetime
import json
from pathlib import Path

import pandas as pd

from .archive_data import BybitPublicArchiveClient
from .features import build_features
from .regimes import (
    classify_regime,
    chronological_fold_ids,
    fit_regime_thresholds,
    summarize_regime,
)
from .training_v3 import trade_path_label


def _frame(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candles)[["open", "high", "low", "close", "volume"]]


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

    feature_rows: list[tuple[int, dict]] = []
    for i in range(lookback - 1, len(candles) - horizon):
        window = candles[i - lookback + 1 : i + 1]
        feature_rows.append((i, build_features(_frame(window))))

    calibration = [
        float(features["realized_vol_20"])
        for _, features in feature_rows[:calibration_rows]
    ]
    thresholds = fit_regime_thresholds(calibration)

    observations: list[dict] = []
    for row_index, (candle_index, features) in enumerate(feature_rows):
        if row_index < calibration_rows:
            continue
        if (row_index - calibration_rows) % stride != 0:
            continue

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


def analyze_regimes(observations: list[dict], *, folds: int = 4) -> dict:
    if not observations:
        raise ValueError("no regime observations")

    timestamps = sorted({int(row["timestamp_ms"]) for row in observations})
    fold_by_timestamp = dict(zip(timestamps, chronological_fold_ids(len(timestamps), folds), strict=True))

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in observations:
        grouped[(str(row["regime"]), str(row["side"]))].append(row)

    summaries = []
    for (regime, side), rows in sorted(grouped.items()):
        values = [float(row["net_return"]) for row in rows]
        fold_ids = [fold_by_timestamp[int(row["timestamp_ms"])] for row in rows]
        summaries.append(asdict(summarize_regime(regime, side, values, fold_ids)))

    qualified = [
        row for row in summaries
        if row["qualified"]
    ]
    ranked = sorted(
        summaries,
        key=lambda row: (
            row["qualified"],
            row["bootstrap_lcb_95"],
            row["mean_net_return"],
            row["trades"],
        ),
        reverse=True,
    )
    return {
        "regime_count": len({row["regime"] for row in summaries}),
        "qualified_count": len(qualified),
        "qualified": qualified,
        "ranked": ranked,
    }


async def run_regime_research(
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
    thresholds, observations = build_regime_observations(candles)
    analysis = analyze_regimes(observations)

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
        "round_trip_cost": 0.0015,
        "barrier_horizon": 12,
        "stop_atr": 1.5,
        "risk_reward": 1.5,
        "volatility_thresholds_calibrated_on_first_rows_only": thresholds,
        "generated_at": datetime.now(UTC).isoformat(),
        **analysis,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "regime-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
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
