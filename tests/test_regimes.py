import math

from triconsensus.regimes import (
    RegimeThresholds,
    bootstrap_lcb,
    classify_regime,
    chronological_fold_ids,
    fit_regime_thresholds,
    summarize_regime,
)


def base_features():
    return {
        "close": 100.0,
        "ema_20": 103.0,
        "ema_50": 99.0,
        "atr_14": 2.0,
        "adx_14": 30.0,
        "realized_vol_20": 0.02,
        "macd_hist": 0.5,
        "roc_10": 0.01,
    }


def test_regime_classifier_combines_trend_volatility_and_momentum():
    state = classify_regime(
        base_features(),
        RegimeThresholds(low_vol=0.01, high_vol=0.03),
    )
    assert state.trend == "trend_up"
    assert state.volatility == "mid_vol"
    assert state.momentum == "momentum_up"
    assert state.key == "trend_up|mid_vol|momentum_up"


def test_range_is_not_forced_into_trend():
    features = base_features()
    features["ema_20"] = 100.1
    features["ema_50"] = 100.0
    features["adx_14"] = 14
    state = classify_regime(
        features,
        RegimeThresholds(low_vol=0.01, high_vol=0.03),
    )
    assert state.trend == "range"


def test_volatility_thresholds_are_data_driven():
    thresholds = fit_regime_thresholds([0.001 + i * 0.001 for i in range(100)])
    assert thresholds.low_vol < thresholds.high_vol
    assert 0.02 < thresholds.low_vol < 0.05
    assert 0.05 < thresholds.high_vol < 0.09


def test_chronological_folds_preserve_time_order():
    ids = chronological_fold_ids(10, 4)
    assert ids == sorted(ids)
    assert set(ids) == {0, 1, 2, 3}


def test_regime_gate_requires_stability_not_one_lucky_fold():
    values = [0.002] * 10 + [-0.004] * 10 + [0.002] * 10 + [0.002] * 10
    folds = [0] * 10 + [1] * 10 + [2] * 10 + [3] * 10
    stats = summarize_regime("x", "LONG", values, folds)
    assert stats.profitable_folds == 3
    assert stats.qualified is False


def test_regime_gate_can_qualify_repeated_positive_edge():
    values = [0.001 + 0.0001 * math.sin(i) for i in range(80)]
    folds = chronological_fold_ids(len(values), 4)
    stats = summarize_regime("x", "LONG", values, folds)
    assert stats.trades == 80
    assert stats.profitable_folds == 4
    assert stats.bootstrap_lcb_95 > 0
    assert stats.qualified is True


def test_bootstrap_lcb_is_deterministic():
    values = [0.001, 0.002, 0.003, 0.004] * 20
    assert bootstrap_lcb(values) == bootstrap_lcb(values)


def test_vectorized_regime_feature_frame_matches_required_columns():
    import pandas as pd
    from triconsensus.regime_research import build_regime_feature_frame

    candles = []
    for i in range(180):
        close = 100 + i * 0.1
        candles.append({
            "start_ms": i * 900000,
            "open": close - 0.05,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close,
            "volume": 100 + (i % 10),
            "turnover": close * (100 + (i % 10)),
        })

    frame = build_regime_feature_frame(candles)
    assert len(frame) == 180
    for column in [
        "ema_20",
        "ema_50",
        "rsi_14",
        "atr_14",
        "adx_14",
        "roc_10",
        "realized_vol_20",
        "macd_hist",
        "vwap",
    ]:
        assert column in frame.columns
    assert pd.notna(frame.iloc[-1]["atr_14"])


def test_hierarchical_regime_analysis_preserves_broad_views():
    from triconsensus.regime_research import analyze_regimes

    observations = []
    for i in range(160):
        observations.append({
            "timestamp_ms": i * 900000,
            "regime": "trend_up|mid_vol|momentum_up",
            "trend": "trend_up",
            "volatility": "mid_vol",
            "momentum": "momentum_up",
            "side": "LONG",
            "net_return": 0.001,
        })
        observations.append({
            "timestamp_ms": i * 900000,
            "regime": "trend_up|mid_vol|momentum_up",
            "trend": "trend_up",
            "volatility": "mid_vol",
            "momentum": "momentum_up",
            "side": "SHORT",
            "net_return": -0.001,
        })

    report = analyze_regimes(observations)
    assert report["qualified_count"] == 1
    assert report["qualified"][0]["side"] == "LONG"
    assert report["hierarchy"]["trend_only"][0]["regime"] == "trend_up"
    assert "trend_volatility" in report["hierarchy"]


def test_regime_research_has_resolved_data_client():
    import triconsensus.regime_research as research

    assert research.BybitKlineArchiveClient is not None


def test_contiguous_mask_rejects_windows_crossing_data_gap():
    from triconsensus.regime_research import contiguous_evaluation_mask

    candles = []
    timestamp = 0
    for i in range(160):
        if i == 90:
            timestamp += 900_000
        candles.append({"start_ms": timestamp})
        timestamp += 900_000

    mask = contiguous_evaluation_mask(
        candles,
        lookback=20,
        horizon=5,
    )

    # Window around the missing candle must be blocked.
    assert mask[89] is False or bool(mask[89]) is False
    assert mask[90] is False or bool(mask[90]) is False
    assert mask[110] is True or bool(mask[110]) is True
