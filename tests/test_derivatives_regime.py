import pandas as pd
import pytest

from triconsensus.derivatives_regime import (
    DERIVATIVES_STATE_VERSION,
    DerivativesState,
    DerivativesThresholds,
    build_derivatives_feature_frame,
    classify_derivatives_state,
    fit_derivatives_thresholds,
)
from triconsensus.derivatives_setup_filter import (
    DERIVATIVES_FILTER_VERSION,
    evaluate_derivatives_filter,
)
from triconsensus.setups import SetupSignal


def sample_rows(n=700):
    rows = []
    for i in range(n):
        rows.append(
            {
                "timestamp_ms": i * 900_000,
                "close": 100 + (i * 0.02),
                "open_interest": 1_000 + (i * 1.5),
                "funding_rate": ((i % 41) - 20) / 1_000_000,
                "long_short_ratio": 1.0 + (((i % 31) - 15) / 200),
            }
        )
    return rows


def state(
    *,
    oi="oi_expansion",
    funding="funding_neutral",
    positioning="positioning_balanced",
):
    return DerivativesState(
        oi_state=oi,
        funding_state=funding,
        positioning_state=positioning,
        price_oi_state="price_up_oi_up",
        key=f"{oi}|{funding}|{positioning}",
        oi_change_4h=0.01,
        funding_rate=0.0001,
        long_short_ratio=1.0,
        price_return_4h=0.01,
    )


def test_versions_are_frozen_before_results():
    assert DERIVATIVES_STATE_VERSION == "v4.2-derivatives-state-frozen-1"
    assert DERIVATIVES_FILTER_VERSION == "v4.2-derivatives-filter-frozen-1"


def test_feature_frame_uses_only_trailing_returns():
    frame = build_derivatives_feature_frame(sample_rows())
    assert pd.isna(frame.iloc[15]["oi_change_4h"])
    assert pd.notna(frame.iloc[16]["oi_change_4h"])
    expected = (
        frame.iloc[16]["open_interest"] / frame.iloc[0]["open_interest"]
    ) - 1
    assert frame.iloc[16]["oi_change_4h"] == pytest.approx(expected)


def test_thresholds_use_calibration_slice_only():
    frame = build_derivatives_feature_frame(sample_rows())
    first = fit_derivatives_thresholds(frame, calibration_rows=480)

    changed = frame.copy()
    changed.loc[480:, "funding_rate"] = 999.0
    changed.loc[480:, "long_short_ratio"] = 999.0
    changed.loc[480:, "oi_change_4h"] = 999.0
    second = fit_derivatives_thresholds(changed, calibration_rows=480)

    assert first == second


def test_state_classifies_expansion_and_crowding():
    thresholds = DerivativesThresholds(
        oi_change_low=-0.01,
        oi_change_high=0.01,
        funding_low=-0.0002,
        funding_high=0.0002,
        ratio_low=0.8,
        ratio_high=1.2,
    )
    result = classify_derivatives_state(
        {
            "oi_change_4h": 0.02,
            "funding_rate": 0.0003,
            "long_short_ratio": 1.3,
            "price_return_4h": 0.01,
        },
        thresholds,
    )
    assert result.oi_state == "oi_expansion"
    assert result.funding_state == "funding_positive_extreme"
    assert result.positioning_state == "long_crowded"
    assert result.price_oi_state == "price_up_oi_up"


def test_trend_and_breakout_require_oi_expansion():
    for setup in ("trend_continuation", "breakout"):
        denied = evaluate_derivatives_filter(
            SetupSignal(setup, "LONG"),
            state(oi="oi_stable"),
        )
        approved = evaluate_derivatives_filter(
            SetupSignal(setup, "LONG"),
            state(oi="oi_expansion"),
        )
        assert denied.allowed is False
        assert approved.allowed is True


def test_long_crowding_veto_overrides_oi_confirmation():
    decision = evaluate_derivatives_filter(
        SetupSignal("trend_continuation", "LONG"),
        state(
            oi="oi_expansion",
            funding="funding_positive_extreme",
            positioning="long_crowded",
        ),
    )
    assert decision.allowed is False
    assert decision.reason == "long_crowding_veto"


def test_range_reversion_requires_opposite_crowding():
    long_signal = SetupSignal("range_reversion", "LONG")
    assert evaluate_derivatives_filter(
        long_signal,
        state(positioning="short_crowded"),
    ).allowed is True
    assert evaluate_derivatives_filter(
        long_signal,
        state(positioning="positioning_balanced"),
    ).allowed is False


def test_missing_derivatives_features_fail_closed():
    thresholds = DerivativesThresholds(
        oi_change_low=-0.01,
        oi_change_high=0.01,
        funding_low=-0.0002,
        funding_high=0.0002,
        ratio_low=0.8,
        ratio_high=1.2,
    )
    with pytest.raises((ValueError, TypeError)):
        classify_derivatives_state(
            {
                "oi_change_4h": None,
                "funding_rate": 0.0,
                "long_short_ratio": 1.0,
                "price_return_4h": 0.0,
            },
            thresholds,
        )
