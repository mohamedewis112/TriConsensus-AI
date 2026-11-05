from triconsensus.setup_regime_research import analyze_setup_regimes


def test_setup_regime_analysis_can_qualify_stable_pair():
    observations = []
    for i in range(120):
        observations.append(
            {
                "timestamp_ms": i * 900_000,
                "setup": "trend_continuation",
                "regime": "trend_up|mid_vol|momentum_up",
                "trend": "trend_up",
                "volatility": "mid_vol",
                "momentum": "momentum_up",
                "side": "LONG",
                "net_return": 0.001,
            }
        )
    report = analyze_setup_regimes(observations)
    assert report["qualified_count"] == 1
    assert report["qualified"][0]["setup"] == "trend_continuation"
    assert report["qualified"][0]["side"] == "LONG"


def test_setup_regime_analysis_keeps_setup_separate():
    observations = []
    for i in range(120):
        observations.append(
            {
                "timestamp_ms": i * 900_000,
                "setup": "trend_continuation",
                "regime": "trend_up|mid_vol|momentum_up",
                "trend": "trend_up",
                "volatility": "mid_vol",
                "momentum": "momentum_up",
                "side": "LONG",
                "net_return": 0.001,
            }
        )
        observations.append(
            {
                "timestamp_ms": i * 900_000,
                "setup": "breakout",
                "regime": "trend_up|mid_vol|momentum_up",
                "trend": "trend_up",
                "volatility": "mid_vol",
                "momentum": "momentum_up",
                "side": "LONG",
                "net_return": -0.001,
            }
        )
    report = analyze_setup_regimes(observations)
    qualified = {(r["setup"], r["side"]) for r in report["qualified"]}
    assert ("trend_continuation", "LONG") in qualified
    assert ("breakout", "LONG") not in qualified


def test_empty_setup_regime_analysis_is_fail_closed():
    report = analyze_setup_regimes([])
    assert report["qualified_count"] == 0
    assert report["qualified"] == []


def test_setup_feature_bundle_satisfies_frozen_generator():
    import math

    from triconsensus.regime_research import build_regime_feature_frame
    from triconsensus.setup_regime_research import _setup_features
    from triconsensus.setups import generate_setups

    candles = []
    for i in range(180):
        close = 100 + i * 0.05 + math.sin(i / 10)
        candles.append(
            {
                "start_ms": i * 900_000,
                "open": close - 0.1,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 100 + (i % 20) * 3,
                "turnover": close * (100 + (i % 20) * 3),
            }
        )

    frame = build_regime_feature_frame(candles)
    features = _setup_features(frame, len(frame) - 1)
    assert "rsi_14" in features
    generate_setups(features)


def test_research_window_ms_is_exact_and_inclusive():
    from datetime import UTC, date, datetime

    from triconsensus.setup_regime_research import research_window_ms

    start_ms, end_ms = research_window_ms(
        days=90,
        end_day=date(2026, 8, 11),
    )
    start = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
    end = datetime.fromtimestamp(end_ms / 1000, tz=UTC)

    assert start.isoformat() == "2026-05-14T00:00:00+00:00"
    assert end.isoformat() == "2026-08-11T23:59:59.999000+00:00"
