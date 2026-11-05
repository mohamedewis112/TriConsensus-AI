from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, date, datetime
import json
from pathlib import Path

import numpy as np

from .kline_archive import BybitKlineArchiveClient, archive_coverage_stats
from .regime_research import (
    _feature_dict,
    build_regime_feature_frame,
    contiguous_evaluation_mask,
)
from .regimes import (
    classify_regime,
    chronological_fold_ids,
    fit_regime_thresholds,
    summarize_regime,
)
from .setups import SETUP_VERSION, generate_setups


HORIZONS = (4, 12, 24, 48)
ROUND_TRIP_COST = 0.0015


def forward_path_metrics(
    candles: list[dict],
    *,
    index: int,
    side: int,
    horizon: int,
    round_trip_cost: float = ROUND_TRIP_COST,
) -> dict:
    if side not in {-1, 1}:
        raise ValueError("side must be -1 or +1")
    if horizon <= 0 or index < 0 or index + horizon >= len(candles):
        raise ValueError("invalid event horizon")

    entry = float(candles[index]["close"])
    future = candles[index + 1 : index + horizon + 1]
    exit_price = float(candles[index + horizon]["close"])

    gross = side * ((exit_price / entry) - 1.0)
    net = gross - round_trip_cost

    if side == 1:
        favorable = max(float(row["high"]) / entry - 1.0 for row in future)
        adverse = min(float(row["low"]) / entry - 1.0 for row in future)
    else:
        favorable = max(1.0 - float(row["low"]) / entry for row in future)
        adverse = min(1.0 - float(row["high"]) / entry for row in future)

    return {
        "gross_return": float(gross),
        "net_return": float(net),
        "mfe": float(favorable),
        "mae": float(adverse),
    }


def _setup_feature_bundle(frame, index: int) -> dict:
    row = frame.iloc[index]
    features = _feature_dict(row)
    for key in ("rsi_14", "volume_z_20", "support_20", "resistance_20"):
        value = float(row[key])
        if not np.isfinite(value):
            raise ValueError(f"non-finite setup feature: {key}")
        features[key] = value
    return features


def build_event_observations(
    candles: list[dict],
    *,
    lookback: int = 120,
    calibration_rows: int = 480,
    stride: int = 4,
    horizons: tuple[int, ...] = HORIZONS,
    round_trip_cost: float = ROUND_TRIP_COST,
) -> tuple[dict, list[dict]]:
    if stride <= 0:
        raise ValueError("stride must be positive")
    if not horizons:
        raise ValueError("at least one horizon is required")
    max_horizon = max(horizons)
    if len(candles) < lookback + calibration_rows + max_horizon + 200:
        raise ValueError("not enough candles for V4.2 event study")

    frame = build_regime_feature_frame(candles)
    contiguous = contiguous_evaluation_mask(
        candles,
        lookback=lookback,
        horizon=max_horizon,
    )
    indices = [
        i
        for i in range(max(lookback - 1, 60), len(candles) - max_horizon)
        if contiguous[i]
        and np.isfinite(float(frame.iloc[i]["realized_vol_20"]))
    ]
    if len(indices) < calibration_rows + 200:
        raise ValueError("not enough contiguous event-study rows")

    thresholds = fit_regime_thresholds(
        [
            float(frame.iloc[i]["realized_vol_20"])
            for i in indices[:calibration_rows]
        ]
    )

    # Freeze event independence at the longest horizon: within each setup+side,
    # no event window may overlap another event window.
    busy_until: dict[tuple[str, str], int] = {}
    observations: list[dict] = []

    for candle_index in indices[calibration_rows::stride]:
        try:
            features = _setup_feature_bundle(frame, candle_index)
        except ValueError:
            continue

        regime = classify_regime(features, thresholds)
        for signal in generate_setups(features):
            key = (signal.name, signal.side)
            if candle_index <= busy_until.get(key, -1):
                continue
            busy_until[key] = candle_index + max_horizon

            side = 1 if signal.side == "LONG" else -1
            for horizon in horizons:
                metrics = forward_path_metrics(
                    candles,
                    index=candle_index,
                    side=side,
                    horizon=horizon,
                    round_trip_cost=round_trip_cost,
                )
                observations.append(
                    {
                        "timestamp_ms": int(candles[candle_index]["start_ms"]),
                        "entry_index": candle_index,
                        "setup": signal.name,
                        "side": signal.side,
                        "regime": regime.key,
                        "trend": regime.trend,
                        "volatility": regime.volatility,
                        "momentum": regime.momentum,
                        "horizon": int(horizon),
                        **metrics,
                    }
                )

    return asdict(thresholds), observations


def _group_event_summary(
    observations: list[dict],
    *,
    key_builder,
    folds: int = 4,
) -> list[dict]:
    timestamps = sorted({int(row["timestamp_ms"]) for row in observations})
    if len(timestamps) < folds:
        return []
    fold_by_timestamp = dict(
        zip(
            timestamps,
            chronological_fold_ids(len(timestamps), folds),
            strict=True,
        )
    )

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in observations:
        groups[(key_builder(row), str(row["side"]))].append(row)

    summaries: list[dict] = []
    for (key, side), rows in sorted(groups.items()):
        values = [float(row["net_return"]) for row in rows]
        fold_ids = [
            fold_by_timestamp[int(row["timestamp_ms"])]
            for row in rows
        ]
        summary = asdict(summarize_regime(key, side, values, fold_ids))
        summary["mean_gross_return"] = float(
            np.mean([float(row["gross_return"]) for row in rows])
        )
        summary["mean_mfe"] = float(np.mean([float(row["mfe"]) for row in rows]))
        summary["mean_mae"] = float(np.mean([float(row["mae"]) for row in rows]))
        summary["median_mfe"] = float(np.median([float(row["mfe"]) for row in rows]))
        summary["median_mae"] = float(np.median([float(row["mae"]) for row in rows]))
        summary["round_trip_cost"] = ROUND_TRIP_COST
        summaries.append(summary)

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


def analyze_event_study(observations: list[dict]) -> dict:
    if not observations:
        return {
            "event_count": 0,
            "qualified_primary_count": 0,
            "qualified_primary": [],
            "setup_horizon": [],
            "setup_regime_horizon": [],
            "entry_edge_passed": False,
        }

    setup_horizon = _group_event_summary(
        observations,
        key_builder=lambda row: f'{row["setup"]}|H{row["horizon"]}',
    )
    setup_regime_horizon = _group_event_summary(
        observations,
        key_builder=lambda row: (
            f'{row["setup"]}|H{row["horizon"]}|{row["regime"]}'
        ),
    )
    qualified_primary = [row for row in setup_horizon if row["qualified"]]

    return {
        "event_count": len(observations),
        "qualified_primary_count": len(qualified_primary),
        "qualified_primary": qualified_primary,
        "setup_horizon": setup_horizon,
        "setup_regime_horizon": setup_regime_horizon,
        "entry_edge_passed": bool(qualified_primary),
    }


async def run_event_study(
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
    quality = archive_coverage_stats(candles, requested_days=days)
    if float(quality["coverage_ratio"]) < 0.90:
        raise ValueError(
            "V4.2 requires >=90% archive coverage; "
            f'got {quality["coverage_ratio"]:.3f}'
        )

    thresholds, observations = build_event_observations(candles)
    analysis = analyze_event_study(observations)

    report = {
        "experiment": "V4.2-ENTRY-EVENT-STUDY",
        "schema_version": 1,
        "setup_version": SETUP_VERSION,
        "symbol": symbol,
        "timeframe": "15m",
        "archive_end_day": end_day.isoformat(),
        "history_days": days,
        "history_start_ms": int(candles[0]["start_ms"]),
        "history_end_ms": int(candles[-1]["start_ms"]),
        "candle_count": len(candles),
        "data_quality": quality,
        "gap_safe_windows_only": True,
        "event_windows_non_overlapping_within_setup_side": True,
        "horizons_candles": list(HORIZONS),
        "horizons_minutes": [int(h * 15) for h in HORIZONS],
        "round_trip_cost": ROUND_TRIP_COST,
        "volatility_thresholds_calibrated_on_first_rows_only": thresholds,
        "generated_at": datetime.now(UTC).isoformat(),
        "default_action": "NO_TRADE",
        **analysis,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "event-study-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TriConsensus V4.2 frozen-entry event study"
    )
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    report = asyncio.run(
        run_event_study(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
