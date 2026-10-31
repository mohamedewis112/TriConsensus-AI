from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path

import pandas as pd

from .v51_forward import (
    CONTEXT_LAG_MINUTES,
    FEATURE,
    HORIZON_MINUTES,
    RELATIONSHIP,
    ROUND_TRIP_RESEARCH_COST,
    STRATEGY_VERSION,
)
from .v51_rule_gate import (
    HIGH_QUANTILE,
    LOW_QUANTILE,
    RuleThresholds,
    _load_frame,
    calibrate_thresholds,
)


CALIBRATION_DAYS = 180
FORWARD_TEST_DAYS = 60


def frozen_protocol() -> dict:
    return {
        "strategy_version": STRATEGY_VERSION,
        "feature": FEATURE,
        "relationship": RELATIONSHIP,
        "calibration_days": CALIBRATION_DAYS,
        "low_quantile": LOW_QUANTILE,
        "high_quantile": HIGH_QUANTILE,
        "horizon_minutes": HORIZON_MINUTES,
        "context_lag_minutes": CONTEXT_LAG_MINUTES,
        "round_trip_cost": ROUND_TRIP_RESEARCH_COST,
        "forward_test_days": FORWARD_TEST_DAYS,
        "threshold_policy": "freeze_for_entire_forward_window",
    }


def protocol_hash() -> str:
    raw = json.dumps(frozen_protocol(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def build_calibration_report(
    *,
    symbol: str,
    end_day: date,
    thresholds: RuleThresholds,
    quality: dict,
    calibration_rows: int,
    last_feature_day: date,
) -> dict:
    if last_feature_day != end_day:
        raise ValueError(
            "calibration does not reach requested end date: "
            f"{last_feature_day.isoformat()} != {end_day.isoformat()}"
        )
    start_day = end_day - timedelta(days=CALIBRATION_DAYS - 1)
    expires_at = datetime.combine(
        end_day + timedelta(days=FORWARD_TEST_DAYS + 1),
        time.min,
        tzinfo=UTC,
    )
    return {
        "experiment": "V5.1-FORWARD-CALIBRATION",
        "schema_version": 1,
        "strategy_version": STRATEGY_VERSION,
        "symbol": symbol,
        "feature_name": FEATURE,
        "relationship": RELATIONSHIP,
        "low_threshold": thresholds.low,
        "high_threshold": thresholds.high,
        "calibration_start": start_day.isoformat(),
        "calibration_end": end_day.isoformat(),
        "horizon_minutes": HORIZON_MINUTES,
        "context_lag_minutes": CONTEXT_LAG_MINUTES,
        "round_trip_cost": ROUND_TRIP_RESEARCH_COST,
        "source": "Bybit 15m archive + Binance Vision USD-M metrics",
        "quality": {
            **quality,
            "calibration_rows": calibration_rows,
            "last_feature_day": last_feature_day.isoformat(),
        },
        "config_hash": protocol_hash(),
        "expires_at": expires_at.isoformat(),
        "forward_test_window": {
            "start": (end_day + timedelta(days=1)).isoformat(),
            "end": (end_day + timedelta(days=FORWARD_TEST_DAYS)).isoformat(),
            "days": FORWARD_TEST_DAYS,
        },
        "protocol": frozen_protocol(),
        "default_action": "NO_TRADE",
    }


async def run_forward_calibration(*, symbol: str, end_day: date, output_dir: Path) -> dict:
    if end_day >= datetime.now(UTC).date():
        raise ValueError("calibration end date must be a completed UTC day")
    frame, quality = await _load_frame(
        symbol=symbol,
        days=CALIBRATION_DAYS,
        end_day=end_day,
    )
    thresholds = calibrate_thresholds(frame)
    valid = frame.loc[
        pd.to_numeric(frame[FEATURE], errors="coerce").notna(),
        ["start_ms", FEATURE],
    ]
    if valid.empty:
        raise ValueError("calibration feature has no valid rows")
    last_feature_day = datetime.fromtimestamp(
        int(valid.iloc[-1]["start_ms"]) / 1000,
        tz=UTC,
    ).date()
    report = build_calibration_report(
        symbol=symbol,
        end_day=end_day,
        thresholds=thresholds,
        quality=quality,
        calibration_rows=len(valid),
        last_feature_day=last_feature_day,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "v51-forward-calibration.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TriConsensus V5.1 frozen forward-paper calibration"
    )
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = asyncio.run(
        run_forward_calibration(
            symbol=args.symbol,
            end_day=date.fromisoformat(args.end_date),
            output_dir=Path(args.output_dir),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
