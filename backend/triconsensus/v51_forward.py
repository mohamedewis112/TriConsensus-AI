from __future__ import annotations

from math import isclose

from .binance_live import V51LiveSignal


STRATEGY_VERSION = "V5.1"
PAPER_EXPERIMENT_VERSION = "V5.1-PAPER"
V51_MODEL_FAMILY = "v5.1_deterministic_cross_venue"
FEATURE = "global_long_short_change_1h"
RELATIONSHIP = "negative"
HORIZON_MINUTES = 180
CONTEXT_LAG_MINUTES = 15
ROUND_TRIP_RESEARCH_COST = 0.0015


class V51CalibrationFailure(RuntimeError):
    pass


def build_v51_strategy_evidence(signal: V51LiveSignal, calibration: dict) -> dict:
    if str(calibration.get("strategy_version") or "") != STRATEGY_VERSION:
        raise V51CalibrationFailure("unexpected strategy version")
    if str(calibration.get("feature_name") or "") != FEATURE:
        raise V51CalibrationFailure("unexpected calibration feature")
    if str(calibration.get("relationship") or "") != RELATIONSHIP:
        raise V51CalibrationFailure("unexpected calibration relationship")
    if int(calibration.get("horizon_minutes") or 0) != HORIZON_MINUTES:
        raise V51CalibrationFailure("unexpected calibration horizon")
    if int(calibration.get("context_lag_minutes") or 0) != CONTEXT_LAG_MINUTES:
        raise V51CalibrationFailure("unexpected context lag")
    if not isclose(
        float(calibration.get("round_trip_cost") or -1),
        ROUND_TRIP_RESEARCH_COST,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise V51CalibrationFailure("unexpected research cost")
    if signal.feature_name != FEATURE:
        raise V51CalibrationFailure("live feature mismatch")
    if signal.signal not in {"LONG", "SHORT", "NO_TRADE"}:
        raise V51CalibrationFailure("invalid live signal")
    if not isclose(float(calibration["low_threshold"]), float(signal.low_threshold), rel_tol=0.0, abs_tol=1e-12):
        raise V51CalibrationFailure("live low threshold differs from calibration")
    if not isclose(float(calibration["high_threshold"]), float(signal.high_threshold), rel_tol=0.0, abs_tol=1e-12):
        raise V51CalibrationFailure("live high threshold differs from calibration")
    return {
        "model_family": V51_MODEL_FAMILY,
        "strategy_version": STRATEGY_VERSION,
        "signal": signal.signal,
        "feature_name": signal.feature_name,
        "feature_value": signal.feature_value,
        "relationship": RELATIONSHIP,
        "horizon_minutes": HORIZON_MINUTES,
        "context_lag_minutes": CONTEXT_LAG_MINUTES,
        "round_trip_research_cost": ROUND_TRIP_RESEARCH_COST,
        "context_timestamp_ms": signal.context_timestamp_ms,
        "context_age_ms": signal.context_age_ms,
        "low_threshold": signal.low_threshold,
        "high_threshold": signal.high_threshold,
        "calibration_id": calibration["id"],
        "calibration_end": str(calibration["calibration_end"]),
        "calibration_config_hash": str(calibration["config_hash"]),
        "reason": signal.reason,
        "default_action": "NO_TRADE",
    }
