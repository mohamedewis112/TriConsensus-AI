from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import UTC, date, datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .binance_vision import BinanceVisionMetricsClient, FuturesMetricRow
from .derivatives_evidence import HORIZONS, score_feature
from .kline_archive import BybitKlineArchiveClient, archive_coverage_stats


PURE_DERIVATIVES_FEATURES = (
    "oi_change_1h",
    "oi_change_4h",
    "global_long_short_log",
    "global_long_short_change_1h",
    "top_account_long_short_log",
    "top_position_long_short_log",
    "top_vs_global_positioning",
    "taker_long_short_log",
    "taker_long_short_change_1h",
)

FEATURE_COLUMNS = (
    "price_return_1h",
    "price_return_4h",
    "oi_change_1h",
    "oi_change_4h",
    "oi_value_change_1h",
    "oi_value_change_4h",
    "global_long_short_log",
    "global_long_short_change_1h",
    "top_account_long_short_log",
    "top_position_long_short_log",
    "top_vs_global_positioning",
    "taker_long_short_log",
    "taker_long_short_change_1h",
    "price_oi_interaction_1h",
    "price_oi_interaction_4h",
)


def metrics_to_15m_frame(rows: list[FuturesMetricRow]) -> pd.DataFrame:
    if not rows:
        raise ValueError("metrics rows cannot be empty")

    frame = pd.DataFrame([asdict(row) for row in rows]).sort_values("timestamp_ms")
    # Binance UM metrics in 2024 are end-of-5m-period labelled. Shift by 1ms
    # before flooring so 00:05, 00:10, 00:15 map to the 00:00-00:15 candle.
    frame["start_ms"] = (
        ((frame["timestamp_ms"].astype("int64") - 1) // 900_000) * 900_000
    )

    grouped = (
        frame.groupby("start_ms", as_index=False)
        .agg(
            sum_open_interest=("sum_open_interest", "last"),
            sum_open_interest_value=("sum_open_interest_value", "last"),
            count_toptrader_long_short_ratio=(
                "count_toptrader_long_short_ratio",
                "last",
            ),
            sum_toptrader_long_short_ratio=(
                "sum_toptrader_long_short_ratio",
                "last",
            ),
            count_long_short_ratio=("count_long_short_ratio", "last"),
            sum_taker_long_short_vol_ratio=(
                "sum_taker_long_short_vol_ratio",
                "mean",
            ),
            metric_rows=("timestamp_ms", "count"),
        )
        .sort_values("start_ms")
    )
    return grouped


def build_cross_venue_frame(
    *,
    candles: list[dict],
    metrics: list[FuturesMetricRow],
) -> tuple[pd.DataFrame, dict]:
    price = pd.DataFrame(candles).copy()
    price["start_ms"] = pd.to_numeric(price["start_ms"]).astype("int64")
    price["close"] = pd.to_numeric(price["close"]).astype(float)
    price = price.sort_values("start_ms").drop_duplicates("start_ms")

    context = metrics_to_15m_frame(metrics)
    frame = price[["start_ms", "close"]].merge(
        context,
        on="start_ms",
        how="left",
    )

    total = max(1, len(frame))
    context_columns = [
        "sum_open_interest",
        "sum_open_interest_value",
        "count_toptrader_long_short_ratio",
        "sum_toptrader_long_short_ratio",
        "count_long_short_ratio",
        "sum_taker_long_short_vol_ratio",
    ]
    coverage = {
        "price_rows": len(frame),
        "context_15m_rows": len(context),
        "matched_context_ratio": float(
            frame["sum_open_interest"].notna().sum() / total
        ),
        "fully_matched_ratio": float(
            frame[context_columns].notna().all(axis=1).sum() / total
        ),
    }

    # Conservative availability guard: a decision at a Bybit candle close may
    # only use derivatives context from the previous completed 15m bucket.
    # This removes boundary/publication-timing ambiguity and prevents accidental
    # same-close leakage from cross-venue metrics.
    frame[context_columns] = frame[context_columns].shift(1)

    frame["price_return_1h"] = frame["close"].pct_change(4)
    frame["price_return_4h"] = frame["close"].pct_change(16)
    frame["oi_change_1h"] = frame["sum_open_interest"].pct_change(4)
    frame["oi_change_4h"] = frame["sum_open_interest"].pct_change(16)
    frame["oi_value_change_1h"] = frame["sum_open_interest_value"].pct_change(4)
    frame["oi_value_change_4h"] = frame["sum_open_interest_value"].pct_change(16)

    for source, target in (
        ("count_long_short_ratio", "global_long_short_log"),
        (
            "count_toptrader_long_short_ratio",
            "top_account_long_short_log",
        ),
        (
            "sum_toptrader_long_short_ratio",
            "top_position_long_short_log",
        ),
        (
            "sum_taker_long_short_vol_ratio",
            "taker_long_short_log",
        ),
    ):
        safe = frame[source].where(frame[source] > 0)
        frame[target] = np.log(safe)

    frame["global_long_short_change_1h"] = (
        frame["global_long_short_log"].diff(4)
    )
    frame["taker_long_short_change_1h"] = (
        frame["taker_long_short_log"].diff(4)
    )
    frame["top_vs_global_positioning"] = (
        frame["top_position_long_short_log"]
        - frame["global_long_short_log"]
    )
    frame["price_oi_interaction_1h"] = (
        frame["price_return_1h"] * frame["oi_change_1h"]
    )
    frame["price_oi_interaction_4h"] = (
        frame["price_return_4h"] * frame["oi_change_4h"]
    )

    for horizon in HORIZONS:
        frame[f"future_return_{horizon}"] = (
            frame["close"].shift(-horizon) / frame["close"] - 1.0
        )

    return frame, coverage


def analyze_cross_venue(frame: pd.DataFrame) -> dict:
    scores = [
        asdict(score_feature(frame, feature=feature, horizon=horizon))
        for feature in FEATURE_COLUMNS
        for horizon in HORIZONS
    ]
    scores.sort(
        key=lambda row: (
            row["qualified"],
            abs(float(row["mean_rank_ic"])),
            float(row["mean_cost_adjusted_decile_spread"]),
        ),
        reverse=True,
    )
    qualified = [row for row in scores if row["qualified"]]
    pure_qualified = [
        row
        for row in qualified
        if row["feature"] in PURE_DERIVATIVES_FEATURES
    ]
    return {
        "qualified_count": len(qualified),
        "qualified": qualified,
        "pure_qualified_count": len(pure_qualified),
        "pure_qualified": pure_qualified,
        "ranked": scores,
    }


async def run_cross_venue_screen(
    *,
    symbol: str,
    days: int,
    end_day: date,
    output_dir: Path,
) -> dict:
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
        raise ValueError(
            "Bybit price coverage below 90%: "
            f'{price_quality["coverage_ratio"]:.3f}'
        )
    if float(context_quality["fully_matched_ratio"]) < 0.85:
        raise ValueError(
            "Binance metrics 15m coverage below 85%: "
            f'{context_quality["fully_matched_ratio"]:.3f}'
        )

    analysis = analyze_cross_venue(frame)
    report = {
        "experiment": "V5-CROSS-VENUE-DERIVATIVES-EVIDENCE",
        "schema_version": 1,
        "target_venue": "Bybit",
        "context_venue": "Binance USD-M Futures",
        "context_source": "Binance Vision static daily metrics",
        "symbol": symbol,
        "timeframe": "15m",
        "history_days": days,
        "window_end": end_day.isoformat(),
        "price_quality": price_quality,
        "context_quality": context_quality,
        "features": list(FEATURE_COLUMNS),
        "horizons_candles": list(HORIZONS),
        "horizons_minutes": [h * 15 for h in HORIZONS],
        "round_trip_cost": 0.0015,
        "context_execution_lag_candles": 1,
        "context_execution_lag_minutes": 15,
        "pure_derivatives_features": list(PURE_DERIVATIVES_FEATURES),
        "qualification": {
            "minimum_abs_mean_rank_ic": 0.03,
            "minimum_same_sign_folds": 3,
            "minimum_profitable_spread_folds": 3,
            "cost_adjusted_spread": "abs(top_decile-bottom_decile)-2x15bps",
        },
        "default_action": "NO_TRADE",
        "generated_at": datetime.now(UTC).isoformat(),
        **analysis,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cross-venue-evidence-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TriConsensus V5 cross-venue derivatives evidence screen"
    )
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    report = asyncio.run(
        run_cross_venue_screen(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
