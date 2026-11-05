from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, date, datetime
import json
from pathlib import Path

from .kline_archive import BybitKlineArchiveClient, archive_coverage_stats
from .regime_research import (
    _feature_dict,
    apply_data_quality_gate,
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
from .training_v3 import trade_path_outcome


def build_setup_observations(
    candles: list[dict],
    *,
    lookback: int = 120,
    horizon: int = 12,
    calibration_rows: int = 480,
    stride: int = 4,
    round_trip_cost: float = 0.0015,
) -> tuple[dict, list[dict]]:
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
    ]
    if len(indices) < calibration_rows + 100:
        raise ValueError("not enough contiguous setup-research rows")

    volatility = [
        float(frame.iloc[i]["realized_vol_20"])
        for i in indices[:calibration_rows]
    ]
    thresholds = fit_regime_thresholds(volatility)

    observations: list[dict] = []
    for candle_index in indices[calibration_rows::stride]:
        row = frame.iloc[candle_index]
        features = _feature_dict(row)
        features.update(
            {
                "rsi_14": float(row["rsi_14"]),
                "volume_z_20": float(row["volume_z_20"]),
                "support_20": float(row["support_20"]),
                "resistance_20": float(row["resistance_20"]),
            }
        )
        regime = classify_regime(features, thresholds)

        for signal in generate_setups(features):
            side = 1 if signal.side == "LONG" else -1
            outcome = trade_path_outcome(
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
                    "entry_index": candle_index,
                    "exit_index": outcome.exit_index,
                    "exit_reason": outcome.exit_reason,
                    "setup": signal.name,
                    "side": signal.side,
                    "regime": regime.key,
                    "trend": regime.trend,
                    "volatility": regime.volatility,
                    "momentum": regime.momentum,
                    "gross_return": float(outcome.gross_return),
                    "net_return": float(outcome.net_return),
                    "round_trip_cost": float(round_trip_cost),
                }
            )

    return asdict(thresholds), observations


def select_non_overlapping(rows: list[dict]) -> list[dict]:
    ordered = sorted(
        rows,
        key=lambda row: (
            int(row["entry_index"]),
            int(row["timestamp_ms"]),
        ),
    )
    selected: list[dict] = []
    busy_until = -1
    for row in ordered:
        entry_index = int(row["entry_index"])
        exit_index = int(row["exit_index"])
        if exit_index < entry_index:
            raise ValueError("exit index cannot precede entry index")
        if entry_index <= busy_until:
            continue
        selected.append(row)
        busy_until = exit_index
    return selected


def execution_diagnostics(rows: list[dict]) -> dict:
    if not rows:
        return {
            "mean_gross_return": 0.0,
            "break_even_round_trip_cost_bps": 0.0,
            "assumed_round_trip_cost_bps": 0.0,
            "cost_margin_bps": 0.0,
            "cost_sensitivity": {},
        }

    mean_gross = sum(float(row["gross_return"]) for row in rows) / len(rows)
    assumed_cost = sum(float(row["round_trip_cost"]) for row in rows) / len(rows)
    break_even_bps = mean_gross * 10_000
    assumed_bps = assumed_cost * 10_000
    sensitivity = {
        str(bps): mean_gross - (bps / 10_000)
        for bps in (5, 8, 10, 12, 15)
    }
    return {
        "mean_gross_return": float(mean_gross),
        "break_even_round_trip_cost_bps": float(break_even_bps),
        "assumed_round_trip_cost_bps": float(assumed_bps),
        "cost_margin_bps": float(break_even_bps - assumed_bps),
        "cost_sensitivity": sensitivity,
    }


def _group_summary(
    observations: list[dict],
    *,
    fold_by_timestamp: dict[int, int],
    key_builder,
) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in observations:
        groups[(key_builder(row), str(row["side"]))].append(row)

    summaries: list[dict] = []
    for (key, side), rows in sorted(groups.items()):
        executable = select_non_overlapping(rows)
        values = [float(row["net_return"]) for row in executable]
        fold_ids = [
            fold_by_timestamp[int(row["timestamp_ms"])]
            for row in executable
        ]
        summary = asdict(
            summarize_regime(
                key,
                side,
                values,
                fold_ids,
            )
        )
        summary.update(execution_diagnostics(executable))
        summary["candidate_signals"] = len(rows)
        summary["overlap_filtered"] = len(rows) - len(executable)
        summary["non_overlapping"] = True
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


def analyze_setup_edge(observations: list[dict], *, folds: int = 4) -> dict:
    if not observations:
        return {
            "setup_signal_count": 0,
            "qualified_count": 0,
            "qualified": [],
            "setup_regime": [],
            "setup_trend_volatility": [],
            "setup_only": [],
        }

    timestamps = sorted({int(row["timestamp_ms"]) for row in observations})
    fold_by_timestamp = dict(
        zip(
            timestamps,
            chronological_fold_ids(len(timestamps), folds),
            strict=True,
        )
    )

    setup_regime = _group_summary(
        observations,
        fold_by_timestamp=fold_by_timestamp,
        key_builder=lambda row: f'{row["setup"]}|{row["regime"]}',
    )
    setup_trend_volatility = _group_summary(
        observations,
        fold_by_timestamp=fold_by_timestamp,
        key_builder=lambda row: (
            f'{row["setup"]}|{row["trend"]}|{row["volatility"]}'
        ),
    )
    setup_only = _group_summary(
        observations,
        fold_by_timestamp=fold_by_timestamp,
        key_builder=lambda row: str(row["setup"]),
    )
    qualified = [row for row in setup_regime if row["qualified"]]
    return {
        "setup_signal_count": len(observations),
        "qualified_count": len(qualified),
        "qualified": qualified,
        "setup_regime": setup_regime,
        "setup_trend_volatility": setup_trend_volatility,
        "setup_only": setup_only,
    }


async def run_setup_research(
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
    thresholds, observations = build_setup_observations(candles)
    analysis = analyze_setup_edge(observations)
    quality = archive_coverage_stats(candles, requested_days=days)

    quality_projection = {
        "qualified_count": analysis["qualified_count"],
        "qualified": analysis["qualified"],
        "ranked": analysis["setup_regime"],
    }
    gated, quality_gate = apply_data_quality_gate(
        quality_projection,
        quality,
    )
    if not quality_gate["passed"]:
        analysis["research_candidates"] = gated.get("research_candidates", [])
        analysis["qualified"] = []
        analysis["qualified_count"] = 0

    report = {
        "experiment": "V4-SETUP-DISCOVERY",
        "schema_version": 1,
        "setup_version": SETUP_VERSION,
        "symbol": symbol,
        "timeframe": "15m",
        "history_days": days,
        "archive_end_day": end_day.isoformat(),
        "history_start_ms": int(candles[0]["start_ms"]),
        "history_end_ms": int(candles[-1]["start_ms"]),
        "candle_count": len(candles),
        "data_quality": quality,
        "data_quality_gate": quality_gate,
        "gap_safe_windows_only": True,
        "round_trip_cost": 0.0015,
        "execution_model": "single_open_trade_non_overlapping",
        "cost_sensitivity_is_diagnostic_only": True,
        "barrier_horizon": 12,
        "stop_atr": 1.5,
        "risk_reward": 1.5,
        "volatility_thresholds": thresholds,
        "generated_at": datetime.now(UTC).isoformat(),
        "default_action": "NO_TRADE",
        "ranked": analysis["setup_regime"],
        **analysis,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "setup-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TriConsensus V4 frozen setup discovery"
    )
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = asyncio.run(
        run_setup_research(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
