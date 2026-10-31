from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .config import settings
from .derivatives_context import BybitDerivativesContextClient
from .market_data import BybitPublicClient


HORIZONS = (4, 12, 24, 48)
FEATURE_COLUMNS = (
    "price_return_1h",
    "price_return_4h",
    "oi_change_1h",
    "oi_change_4h",
    "long_short_log_ratio",
    "long_short_change_1h",
    "premium_z_1d",
    "funding_rate",
    "price_oi_interaction_1h",
    "price_oi_interaction_4h",
)


@dataclass(frozen=True)
class EvidenceScore:
    feature: str
    horizon: int
    rows: int
    mean_rank_ic: float
    median_rank_ic: float
    same_sign_folds: int
    folds: int
    mean_cost_adjusted_decile_spread: float
    profitable_spread_folds: int
    qualified: bool


def research_window_ms(*, days: int, end_day: date) -> tuple[int, int]:
    if days < 7:
        raise ValueError("research window must be at least 7 days")
    first_day = end_day - timedelta(days=days - 1)
    start = datetime.combine(first_day, time.min, tzinfo=UTC)
    end = datetime.combine(
        end_day + timedelta(days=1),
        time.min,
        tzinfo=UTC,
    ) - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _series_frame(points, name: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_ms": [int(point.timestamp_ms) for point in points],
            name: [float(point.value) for point in points],
        }
    ).sort_values("start_ms")


def build_context_frame(
    *,
    candles: list[dict],
    context: dict,
) -> tuple[pd.DataFrame, dict]:
    price = pd.DataFrame(candles).copy()
    price["start_ms"] = pd.to_numeric(price["start_ms"]).astype("int64")
    price["close"] = pd.to_numeric(price["close"]).astype(float)
    price = price.sort_values("start_ms").drop_duplicates("start_ms")

    oi = _series_frame(context["open_interest"], "open_interest")
    premium = _series_frame(context["premium_index"], "premium_index")

    ratio_points = context["account_ratio"]
    ratio = pd.DataFrame(
        {
            "start_ms": [int(point.timestamp_ms) for point in ratio_points],
            "buy_ratio": [float(point.buy_ratio) for point in ratio_points],
            "sell_ratio": [float(point.sell_ratio) for point in ratio_points],
        }
    ).sort_values("start_ms")

    funding = _series_frame(context["funding"], "funding_rate")

    frame = price[["start_ms", "close"]].copy()
    frame = frame.merge(oi, on="start_ms", how="left")
    frame = frame.merge(ratio, on="start_ms", how="left")
    frame = frame.merge(premium, on="start_ms", how="left")

    if funding.empty:
        frame["funding_rate"] = np.nan
    else:
        frame = pd.merge_asof(
            frame.sort_values("start_ms"),
            funding.sort_values("start_ms"),
            on="start_ms",
            direction="backward",
        )

    total = max(1, len(frame))
    coverage = {
        "price_rows": len(frame),
        "open_interest": float(frame["open_interest"].notna().sum() / total),
        "account_ratio": float(
            (
                frame["buy_ratio"].notna()
                & frame["sell_ratio"].notna()
            ).sum()
            / total
        ),
        "premium_index": float(frame["premium_index"].notna().sum() / total),
        "funding_asof": float(frame["funding_rate"].notna().sum() / total),
    }

    frame["price_return_1h"] = frame["close"].pct_change(4)
    frame["price_return_4h"] = frame["close"].pct_change(16)
    frame["oi_change_1h"] = frame["open_interest"].pct_change(4)
    frame["oi_change_4h"] = frame["open_interest"].pct_change(16)

    safe_sell = frame["sell_ratio"].replace(0, np.nan)
    frame["long_short_log_ratio"] = np.log(frame["buy_ratio"] / safe_sell)
    frame["long_short_change_1h"] = frame["long_short_log_ratio"].diff(4)

    premium_mean = frame["premium_index"].rolling(96, min_periods=48).mean()
    premium_std = (
        frame["premium_index"]
        .rolling(96, min_periods=48)
        .std()
        .replace(0, np.nan)
    )
    frame["premium_z_1d"] = (
        frame["premium_index"] - premium_mean
    ) / premium_std

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


def _rank_ic(feature: pd.Series, target: pd.Series) -> float:
    x = feature.rank(method="average")
    y = target.rank(method="average")
    value = x.corr(y)
    return float(value) if pd.notna(value) else 0.0


def _decile_spread(
    feature: pd.Series,
    target: pd.Series,
    *,
    round_trip_cost: float,
) -> float:
    n = len(feature)
    if n < 50:
        return 0.0
    ranks = feature.rank(method="first", pct=True)
    low = target[ranks <= 0.10]
    high = target[ranks >= 0.90]
    if len(low) < 5 or len(high) < 5:
        return 0.0
    gross_spread = float(high.mean() - low.mean())
    # A long-short decile spread contains two executable legs.
    return abs(gross_spread) - (2 * round_trip_cost)


def score_feature(
    frame: pd.DataFrame,
    *,
    feature: str,
    horizon: int,
    folds: int = 4,
    round_trip_cost: float = 0.0015,
) -> EvidenceScore:
    target = f"future_return_{horizon}"
    data = frame[[feature, target]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(data) < 400:
        return EvidenceScore(
            feature=feature,
            horizon=horizon,
            rows=len(data),
            mean_rank_ic=0.0,
            median_rank_ic=0.0,
            same_sign_folds=0,
            folds=0,
            mean_cost_adjusted_decile_spread=0.0,
            profitable_spread_folds=0,
            qualified=False,
        )

    boundaries = np.linspace(0, len(data), folds + 1, dtype=int)
    ics: list[float] = []
    spreads: list[float] = []
    for fold in range(folds):
        part = data.iloc[boundaries[fold] : boundaries[fold + 1]]
        if len(part) < 80:
            continue
        ics.append(_rank_ic(part[feature], part[target]))
        spreads.append(
            _decile_spread(
                part[feature],
                part[target],
                round_trip_cost=round_trip_cost,
            )
        )

    if len(ics) < 3:
        return EvidenceScore(
            feature=feature,
            horizon=horizon,
            rows=len(data),
            mean_rank_ic=float(np.mean(ics)) if ics else 0.0,
            median_rank_ic=float(np.median(ics)) if ics else 0.0,
            same_sign_folds=0,
            folds=len(ics),
            mean_cost_adjusted_decile_spread=(
                float(np.mean(spreads)) if spreads else 0.0
            ),
            profitable_spread_folds=sum(1 for x in spreads if x > 0),
            qualified=False,
        )

    mean_ic = float(np.mean(ics))
    direction = 1 if mean_ic > 0 else -1 if mean_ic < 0 else 0
    same_sign = sum(1 for value in ics if value * direction > 0)
    profitable = sum(1 for value in spreads if value > 0)
    mean_spread = float(np.mean(spreads))

    qualified = (
        abs(mean_ic) >= 0.03
        and same_sign >= 3
        and profitable >= 3
        and mean_spread > 0
    )
    return EvidenceScore(
        feature=feature,
        horizon=horizon,
        rows=len(data),
        mean_rank_ic=mean_ic,
        median_rank_ic=float(np.median(ics)),
        same_sign_folds=same_sign,
        folds=len(ics),
        mean_cost_adjusted_decile_spread=mean_spread,
        profitable_spread_folds=profitable,
        qualified=qualified,
    )


def analyze_evidence(frame: pd.DataFrame) -> dict:
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
    return {
        "qualified_count": len(qualified),
        "qualified": qualified,
        "ranked": scores,
    }


async def run_screen(
    *,
    symbol: str,
    days: int,
    end_day: date,
    output_dir: Path,
) -> dict:
    start_ms, end_ms = research_window_ms(days=days, end_day=end_day)
    price_client = BybitPublicClient(settings)
    context_client = BybitDerivativesContextClient(settings.bybit_base_url)

    candles, context = await asyncio.gather(
        price_client.historical_klines(
            symbol,
            "15m",
            start_ms=start_ms,
            end_ms=end_ms,
        ),
        context_client.snapshot_history(
            symbol,
            start_ms=start_ms,
            end_ms=end_ms,
        ),
    )

    frame, coverage = build_context_frame(candles=candles, context=context)
    minimum_context_coverage = min(
        coverage["open_interest"],
        coverage["account_ratio"],
        coverage["premium_index"],
        coverage["funding_asof"],
    )
    if minimum_context_coverage < 0.85:
        raise ValueError(
            "derivatives context coverage below 85%: "
            f"{minimum_context_coverage:.3f}"
        )

    analysis = analyze_evidence(frame)
    report = {
        "experiment": "V5-DERIVATIVES-EVIDENCE-SCREEN",
        "schema_version": 1,
        "symbol": symbol,
        "timeframe": "15m",
        "history_days": days,
        "window_end": end_day.isoformat(),
        "history_start_ms": start_ms,
        "history_end_ms": end_ms,
        "price_rows": len(candles),
        "context_coverage": coverage,
        "minimum_context_coverage": minimum_context_coverage,
        "features": list(FEATURE_COLUMNS),
        "horizons_candles": list(HORIZONS),
        "horizons_minutes": [h * 15 for h in HORIZONS],
        "round_trip_cost": 0.0015,
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
    (output_dir / "derivatives-evidence-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TriConsensus V5 derivatives evidence screen"
    )
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    report = asyncio.run(
        run_screen(
            symbol=args.symbol,
            days=args.days,
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
