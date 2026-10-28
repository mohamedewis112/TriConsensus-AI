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
