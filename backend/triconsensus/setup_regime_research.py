from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
import json
from pathlib import Path

import numpy as np

from .config import settings
from .kline_archive import BybitKlineArchiveClient, archive_coverage_stats
from .market_data import BybitPublicClient
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
from .training_v3 import trade_path_label


SETUP_FEATURE_COLUMNS = (
    "rsi_14",
    "vwap",
    "support_20",
    "resistance_20",
    "volume_z_20",
)


def _setup_features(frame, index: int) -> dict:
    features = _feature_dict(frame.iloc[index])
    for key in SETUP_FEATURE_COLUMNS:
        value = float(frame.iloc[index][key])
        if not np.isfinite(value):
            raise ValueError(f"non-finite setup feature: {key}")
        features[key] = value
    return features


def build_setup_regime_observations(
    candles: list[dict],
    *,
    lookback: int = 120,
    horizon: int = 12,
    calibration_rows: int = 480,
    round_trip_cost: float = 0.0015,
) -> tuple[dict, list[dict]]:
    if len(candles) < lookback + horizon + calibration_rows + 200:
        raise ValueError("not enough candles for V4.1 setup-regime research")

    frame = build_regime_feature_frame(candles)
    contiguous = contiguous_evaluation_mask(
        candles,
        lookback=lookback,
        horizon=horizon,
    )

    indices = [
        i
        for i in range(max(lookback - 1, 60), len(candles) - horizon)
        if contiguous[i]
        and np.isfinite(float(frame.iloc[i]["realized_vol_20"]))
    ]
    if len(indices) < calibration_rows + 200:
        raise ValueError("not enough contiguous feature rows for V4.1")

    calibration_indices = indices[:calibration_rows]
    thresholds = fit_regime_thresholds(
        [float(frame.iloc[i]["realized_vol_20"]) for i in calibration_indices]
    )

    # Each setup is evaluated as its own strategy. A setup cannot open another
    # position until the previous 12-candle evaluation horizon has expired.
    last_entry_by_setup: dict[str, int] = {}
    observations: list[dict] = []

    for candle_index in indices[calibration_rows:]:
        try:
            features = _setup_features(frame, candle_index)
        except ValueError:
            continue

        regime = classify_regime(features, thresholds)
        for signal in generate_setups(features):
            previous = last_entry_by_setup.get(signal.name)
            if previous is not None and candle_index - previous < horizon:
                continue

            side = 1 if signal.side == "LONG" else -1
            _, net_return = trade_path_label(
                candles,
                index=candle_index,
                side=side,
                atr=float(features["atr_14"]),
                horizon=horizon,
                round_trip_cost=round_trip_cost,
            )
            last_entry_by_setup[signal.name] = candle_index
            observations.append(
                {
                    "timestamp_ms": int(candles[candle_index]["start_ms"]),
                    "setup": signal.name,
                    "regime": regime.key,
                    "trend": regime.trend,
                    "volatility": regime.volatility,
                    "momentum": regime.momentum,
                    "side": signal.side,
                    "net_return": float(net_return),
                }
            )

    return asdict(thresholds), observations


def _summarize(
    observations: list[dict],
    *,
    key_mode: str,
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

    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in observations:
        if key_mode == "full":
            regime = str(row["regime"])
        elif key_mode == "trend_volatility":
            regime = f'{row["trend"]}|{row["volatility"]}'
        elif key_mode == "trend":
            regime = str(row["trend"])
        elif key_mode == "setup_only":
            regime = "ALL_REGIMES"
        else:
            raise ValueError(f"unsupported key_mode: {key_mode}")

        grouped[(str(row["setup"]), regime, str(row["side"]))].append(row)

    results: list[dict] = []
    for (setup, regime, side), rows in sorted(grouped.items()):
        values = [float(row["net_return"]) for row in rows]
        fold_ids = [
            fold_by_timestamp[int(row["timestamp_ms"])]
            for row in rows
        ]
        stats = asdict(
            summarize_regime(
                regime,
                side,
                values,
                fold_ids,
            )
        )
        stats["setup"] = setup
        results.append(stats)

    return sorted(
        results,
        key=lambda row: (
            row["qualified"],
            row["bootstrap_lcb_95"],
            row["mean_net_return"],
            row["trades"],
        ),
        reverse=True,
    )


def analyze_setup_regimes(observations: list[dict]) -> dict:
    if not observations:
        return {
            "signal_count": 0,
            "setup_signal_counts": {},
            "qualified_count": 0,
            "qualified": [],
            "ranked": [],
            "hierarchy": {
                "setup_only": [],
                "trend": [],
                "trend_volatility": [],
            },
        }

    full = _summarize(observations, key_mode="full")
    qualified = [row for row in full if row["qualified"]]
    setup_names = sorted({str(row["setup"]) for row in observations})

    return {
        "signal_count": len(observations),
        "setup_signal_counts": {
            setup: sum(1 for row in observations if row["setup"] == setup)
            for setup in setup_names
        },
        "qualified_count": len(qualified),
        "qualified": qualified,
        "ranked": full,
        "hierarchy": {
            "setup_only": _summarize(observations, key_mode="setup_only"),
            "trend": _summarize(observations, key_mode="trend"),
            "trend_volatility": _summarize(
                observations,
                key_mode="trend_volatility",
            ),
        },
    }


def research_window_ms(*, days: int, end_day: date) -> tuple[int, int]:
    if days < 1:
        raise ValueError("days must be positive")
    first_day = end_day - timedelta(days=days - 1)
    start_dt = datetime.combine(first_day, time.min, tzinfo=UTC)
    end_dt = datetime.combine(
        end_day + timedelta(days=1),
        time.min,
        tzinfo=UTC,
    ) - timedelta(milliseconds=1)
    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


async def load_research_candles(
    *,
    symbol: str,
    days: int,
    end_day: date,
    source: str,
) -> list[dict]:
    if source == "archive":
        return await BybitKlineArchiveClient().historical_15m(
            symbol,
            days=days,
            end_day=end_day,
        )
    if source == "api":
        start_ms, end_ms = research_window_ms(days=days, end_day=end_day)
        return await BybitPublicClient(settings).historical_klines(
            symbol,
            "15m",
            start_ms=start_ms,
            end_ms=end_ms,
        )
    raise ValueError(f"unsupported research source: {source}")


async def run_setup_regime_research(
    *,
    symbol: str,
    days: int,
    end_day: date,
    output_dir: Path,
    source: str = "archive",
) -> dict:
    candles = await load_research_candles(
        symbol=symbol,
        days=days,
        end_day=end_day,
        source=source,
    )
    quality = archive_coverage_stats(candles, requested_days=days)
    if float(quality["coverage_ratio"]) < 0.90:
        raise ValueError(
            "V4.1 requires >=90% archive coverage; "
            f'got {quality["coverage_ratio"]:.3f}'
        )

    thresholds, observations = build_setup_regime_observations(candles)
    analysis = analyze_setup_regimes(observations)

    report = {
        "experiment": "V4.1-SETUP-REGIME-DISCOVERY",
        "schema_version": 1,
        "setup_version": SETUP_VERSION,
        "symbol": symbol,
        "timeframe": "15m",
        "archive_end_day": end_day.isoformat(),
        "history_days": days,
        "data_source": source,
        "history_start_ms": int(candles[0]["start_ms"]),
        "history_end_ms": int(candles[-1]["start_ms"]),
        "candle_count": len(candles),
        "data_quality": quality,
        "gap_safe_windows_only": True,
        "setup_cooldown_candles": 12,
        "round_trip_cost": 0.0015,
        "barrier_horizon": 12,
        "stop_atr": 1.5,
        "risk_reward": 1.5,
        "volatility_thresholds_calibrated_on_first_rows_only": thresholds,
        "generated_at": datetime.now(UTC).isoformat(),
        **analysis,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "setup-regime-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "qualified-policy.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "setup_version": SETUP_VERSION,
                "symbol": symbol,
                "default_action": "NO_TRADE",
                "qualified_setup_regime_pairs": report["qualified"],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TriConsensus V4.1 setup x regime discovery"
    )
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--source",
        choices=["archive", "api"],
        default="archive",
    )
    args = parser.parse_args()

    report = asyncio.run(
        run_setup_regime_research(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
            source=args.source,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
