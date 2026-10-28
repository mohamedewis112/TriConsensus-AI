from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .binance_vision import BinanceVisionMetricsClient
from .cross_venue_evidence import build_cross_venue_frame
from .kline_archive import BybitKlineArchiveClient, archive_coverage_stats
from .regimes import bootstrap_lcb, chronological_fold_ids


FEATURE = "global_long_short_change_1h"
HORIZON = 12
LOW_QUANTILE = 0.10
HIGH_QUANTILE = 0.90
ROUND_TRIP_COST = 0.0015


@dataclass(frozen=True)
class RuleThresholds:
    low: float
    high: float


def calibrate_thresholds(frame: pd.DataFrame) -> RuleThresholds:
    values = (
        frame[FEATURE]
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .astype(float)
    )
    if len(values) < 1000:
        raise ValueError("not enough calibration rows")
    low = float(values.quantile(LOW_QUANTILE))
    high = float(values.quantile(HIGH_QUANTILE))
    if not np.isfinite(low) or not np.isfinite(high) or low >= high:
        raise ValueError("invalid frozen thresholds")
    return RuleThresholds(low=low, high=high)


def _profit_factor(returns: list[float]) -> float | None:
    gains = sum(value for value in returns if value > 0)
    losses = abs(sum(value for value in returns if value < 0))
    if losses == 0:
        return None
    return gains / losses


def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= max(1e-9, 1.0 + value)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak
        worst = max(worst, drawdown)
    return float(worst)


def execute_frozen_rule(
    frame: pd.DataFrame,
    thresholds: RuleThresholds,
    *,
    horizon: int = HORIZON,
    round_trip_cost: float = ROUND_TRIP_COST,
) -> list[dict]:
    required = [FEATURE, "close", "start_ms"]
    data = frame.copy()
    for column in required:
        if column not in data.columns:
            raise ValueError(f"missing rule column: {column}")

    rows: list[dict] = []
    busy_until = -1

    for index in range(0, len(data) - horizon):
        if index <= busy_until:
            continue

        feature = float(data.iloc[index][FEATURE])
        if not np.isfinite(feature):
            continue

        if feature <= thresholds.low:
            side = "LONG"
            direction = 1
        elif feature >= thresholds.high:
            side = "SHORT"
            direction = -1
        else:
            continue

        entry = float(data.iloc[index]["close"])
        exit_price = float(data.iloc[index + horizon]["close"])
        if entry <= 0 or exit_price <= 0:
            continue

        gross = direction * ((exit_price / entry) - 1.0)
        net = gross - round_trip_cost
        rows.append(
            {
                "entry_index": index,
                "exit_index": index + horizon,
                "timestamp_ms": int(data.iloc[index]["start_ms"]),
                "feature_value": feature,
                "side": side,
                "gross_return": float(gross),
                "net_return": float(net),
            }
        )
        busy_until = index + horizon

    return rows


def evaluate_rule(trades: list[dict], *, folds: int = 4) -> dict:
    if not trades:
        return {
            "trade_count": 0,
            "qualified": False,
            "reason": "no_trades",
        }

    returns = [float(row["net_return"]) for row in trades]
    gross = [float(row["gross_return"]) for row in trades]
    fold_ids = chronological_fold_ids(len(trades), folds)
    fold_means = []
    for fold in sorted(set(fold_ids)):
        values = [
            value
            for value, fold_id in zip(returns, fold_ids, strict=True)
            if fold_id == fold
        ]
        fold_means.append(float(np.mean(values)))

    profitable_folds = sum(1 for value in fold_means if value > 0)
    mean_net = float(np.mean(returns))
    lcb = bootstrap_lcb(returns, samples=2000, seed=42)
    pf = _profit_factor(returns)
    max_dd = _max_drawdown(returns)
    longs = sum(1 for row in trades if row["side"] == "LONG")
    shorts = len(trades) - longs

    qualified = (
        len(trades) >= 80
        and mean_net >= 0.0005
        and profitable_folds >= 3
        and lcb > 0
        and pf is not None
        and pf >= 1.20
    )

    return {
        "trade_count": len(trades),
        "long_trades": longs,
        "short_trades": shorts,
        "hit_rate": float(np.mean([value > 0 for value in returns])),
        "mean_gross_return": float(np.mean(gross)),
        "mean_net_return": mean_net,
        "median_net_return": float(np.median(returns)),
        "profit_factor": pf,
        "profitable_folds": profitable_folds,
        "folds": len(fold_means),
        "fold_means": fold_means,
        "bootstrap_lcb_95": lcb,
        "max_drawdown": max_dd,
        "cumulative_compounded_return": float(np.prod([1.0 + x for x in returns]) - 1.0),
        "qualified": qualified,
        "gate": {
            "minimum_trades": 80,
            "minimum_mean_net_return": 0.0005,
            "minimum_profitable_folds": 3,
            "requires_positive_bootstrap_lcb": True,
            "minimum_profit_factor": 1.20,
        },
    }


async def _load_frame(
    *,
    symbol: str,
    days: int,
    end_day: date,
) -> tuple[pd.DataFrame, dict]:
    candles, metrics = await asyncio.gather(
        BybitKlineArchiveClient().historical_15m(
            symbol,
            days=days,
            end_day=end_day,
        ),
        BinanceVisionMetricsClient().historical_metrics(
            symbol,
            days=days,
            end_day=end_day,
        ),
    )
    price_quality = archive_coverage_stats(candles, requested_days=days)
    frame, context_quality = build_cross_venue_frame(
        candles=candles,
        metrics=metrics,
    )
    if float(price_quality["coverage_ratio"]) < 0.90:
        raise ValueError("price coverage below 90%")
    if float(context_quality["fully_matched_ratio"]) < 0.85:
        raise ValueError("context coverage below 85%")
    return frame, {
        "price": price_quality,
        "context": context_quality,
    }


async def run_rule_gate(
    *,
    symbol: str,
    calibration_days: int,
    calibration_end_day: date,
    test_days: int,
    test_end_day: date,
    output_dir: Path,
) -> dict:
    calibration_frame, calibration_quality = await _load_frame(
        symbol=symbol,
        days=calibration_days,
        end_day=calibration_end_day,
    )
    thresholds = calibrate_thresholds(calibration_frame)

    test_frame, test_quality = await _load_frame(
        symbol=symbol,
        days=test_days,
        end_day=test_end_day,
    )
    trades = execute_frozen_rule(test_frame, thresholds)
    evaluation = evaluate_rule(trades)

    report = {
        "experiment": "V5.1-UNTOUCHED-DIRECTIONAL-RULE-GATE",
        "schema_version": 1,
        "symbol": symbol,
        "target_venue": "Bybit",
        "context_venue": "Binance USD-M Futures",
        "feature": FEATURE,
        "relationship": "negative",
        "rule": "bottom-decile => LONG; top-decile => SHORT",
        "horizon_candles": HORIZON,
        "horizon_minutes": HORIZON * 15,
        "context_execution_lag_minutes": 15,
        "round_trip_cost": ROUND_TRIP_COST,
        "calibration": {
            "days": calibration_days,
            "end_date": calibration_end_day.isoformat(),
            "low_quantile": LOW_QUANTILE,
            "high_quantile": HIGH_QUANTILE,
            "thresholds": asdict(thresholds),
            "quality": calibration_quality,
        },
        "untouched_test": {
            "days": test_days,
            "end_date": test_end_day.isoformat(),
            "quality": test_quality,
        },
        "default_action": "NO_TRADE",
        **evaluation,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "v51-rule-gate.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TriConsensus V5.1 untouched directional rule gate"
    )
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--calibration-days", type=int, default=180)
    parser.add_argument("--calibration-end-date", default="2024-11-30")
    parser.add_argument("--test-days", type=int, default=60)
    parser.add_argument("--test-end-date", default="2024-05-31")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    report = asyncio.run(
        run_rule_gate(
            symbol=args.symbol,
            calibration_days=args.calibration_days,
            calibration_end_day=date.fromisoformat(args.calibration_end_date),
            test_days=args.test_days,
            test_end_day=date.fromisoformat(args.test_end_date),
            output_dir=Path(args.output_dir),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
