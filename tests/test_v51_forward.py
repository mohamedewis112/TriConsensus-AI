from datetime import UTC, date, datetime

import pytest

from triconsensus.binance_live import V51LiveSignal
from triconsensus.config import Settings
from triconsensus.cycle import PaperDecisionCycle
from triconsensus.db import SupabaseRepository
from triconsensus.v51_forward import (
    V51CalibrationFailure,
    V51_MODEL_FAMILY,
    build_v51_strategy_evidence,
)
from triconsensus.v51_forward_calibration import (
    FORWARD_TEST_DAYS,
    build_calibration_report,
    protocol_hash,
)
from triconsensus.v51_rule_gate import RuleThresholds


FROZEN_PAPER_EXPERIMENT_CONFIG_HASH = (
    "b76546273434b348543fb4cd92d86ac0842e8b48c374fcaa83f37c8e08cfc2d0"
)


def calibration():
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "strategy_version": "V5.1",
        "feature_name": "global_long_short_change_1h",
        "relationship": "negative",
        "low_threshold": -0.03,
        "high_threshold": 0.03,
        "calibration_end": "2026-10-26",
        "horizon_minutes": 180,
        "context_lag_minutes": 15,
        "round_trip_cost": 0.0015,
        "config_hash": "abc123",
    }


def live_signal(signal="LONG"):
    return V51LiveSignal(
        symbol="BTCUSDT",
        signal=signal,
        feature_name="global_long_short_change_1h",
        feature_value=-0.04,
        context_timestamp_ms=1_700_000_000_000,
        context_age_ms=900_000,
        low_threshold=-0.03,
        high_threshold=0.03,
        reason="bottom_decile_inverse_signal",
    )


def test_v51_evidence_accepts_only_frozen_protocol():
    evidence = build_v51_strategy_evidence(live_signal(), calibration())
    assert evidence["model_family"] == V51_MODEL_FAMILY
    assert evidence["signal"] == "LONG"
    assert evidence["horizon_minutes"] == 180


def test_v51_evidence_rejects_protocol_drift():
    row = calibration()
    row["horizon_minutes"] = 240
    with pytest.raises(V51CalibrationFailure, match="horizon"):
        build_v51_strategy_evidence(live_signal(), row)


def test_forward_calibration_freezes_60_day_window():
    report = build_calibration_report(
        symbol="BTCUSDT",
        end_day=date(2026, 10, 26),
        thresholds=RuleThresholds(low=-0.03, high=0.03),
        quality={
            "price": {"coverage_ratio": 1.0},
            "context": {"fully_matched_ratio": 1.0},
        },
        calibration_rows=17000,
        last_feature_day=date(2026, 10, 26),
    )
    assert report["config_hash"] == protocol_hash()
    assert report["forward_test_window"]["days"] == FORWARD_TEST_DAYS
    assert report["forward_test_window"]["start"] == "2026-10-27"
    assert report["expires_at"] == datetime(2026, 12, 26, tzinfo=UTC).isoformat()


def test_v51_paper_experiment_runtime_config_is_frozen():
    cycle = object.__new__(PaperDecisionCycle)
    cycle.settings = Settings(_env_file=None)

    digest = SupabaseRepository.config_hash(cycle.experiment_config())

    assert digest == FROZEN_PAPER_EXPERIMENT_CONFIG_HASH
