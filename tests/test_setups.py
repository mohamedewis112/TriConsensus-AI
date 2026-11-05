from triconsensus.setups import SETUP_VERSION, generate_setups


def base():
    return {
        "close": 105.0,
        "ema_20": 103.0,
        "ema_50": 100.0,
        "rsi_14": 60.0,
        "atr_14": 2.0,
        "adx_14": 25.0,
        "macd_hist": 0.5,
        "roc_10": 0.01,
        "volume_z_20": 1.0,
        "support_20": 98.0,
        "resistance_20": 104.0,
    }


def test_setup_version_is_frozen():
    assert SETUP_VERSION == "v4.1-frozen-quant-setups"


def test_trend_continuation_and_breakout_can_coexist():
    signals = generate_setups(base())
    pairs = {(signal.name, signal.side) for signal in signals}
    assert ("trend_continuation", "LONG") in pairs
    assert ("breakout", "LONG") in pairs


def test_short_rules_are_symmetric():
    features = base()
    features.update({
        "close": 95.0,
        "ema_20": 97.0,
        "ema_50": 100.0,
        "rsi_14": 40.0,
        "macd_hist": -0.5,
        "roc_10": -0.01,
        "support_20": 96.0,
        "resistance_20": 102.0,
    })
    signals = generate_setups(features)
    pairs = {(signal.name, signal.side) for signal in signals}
    assert ("trend_continuation", "SHORT") in pairs
    assert ("breakout", "SHORT") in pairs


def test_range_reversion_requires_weak_trend():
    features = base()
    features.update({
        "close": 98.2,
        "ema_20": 100.0,
        "ema_50": 100.2,
        "rsi_14": 30.0,
        "adx_14": 12.0,
        "support_20": 98.0,
        "resistance_20": 103.0,
        "macd_hist": -0.1,
        "roc_10": -0.005,
    })
    signals = generate_setups(features)
    assert ("range_reversion", "LONG") in {
        (signal.name, signal.side) for signal in signals
    }


def test_no_setup_when_conditions_are_ambiguous():
    features = base()
    features.update({
        "close": 101.0,
        "ema_20": 101.0,
        "ema_50": 101.0,
        "rsi_14": 50.0,
        "adx_14": 19.0,
        "macd_hist": 0.0,
        "roc_10": 0.0,
        "volume_z_20": 0.0,
        "support_20": 98.0,
        "resistance_20": 104.0,
    })
    assert generate_setups(features) == []


def test_non_overlap_keeps_only_executable_signals():
    from triconsensus.setup_research import select_non_overlapping

    rows = [
        {"entry_index": 10, "exit_index": 14, "timestamp_ms": 10},
        {"entry_index": 12, "exit_index": 15, "timestamp_ms": 12},
        {"entry_index": 15, "exit_index": 18, "timestamp_ms": 15},
        {"entry_index": 19, "exit_index": 20, "timestamp_ms": 19},
    ]
    selected = select_non_overlapping(rows)
    assert [row["entry_index"] for row in selected] == [10, 15, 19]


def test_execution_diagnostics_reports_break_even_cost():
    import pytest
    from triconsensus.setup_research import execution_diagnostics

    rows = [
        {
            "gross_return": 0.0020,
            "net_return": 0.0005,
            "round_trip_cost": 0.0015,
        },
        {
            "gross_return": 0.0010,
            "net_return": -0.0005,
            "round_trip_cost": 0.0015,
        },
    ]
    diagnostics = execution_diagnostics(rows)
    assert diagnostics["mean_gross_return"] == pytest.approx(0.0015)
    assert diagnostics["break_even_round_trip_cost_bps"] == pytest.approx(15.0)
    assert diagnostics["assumed_round_trip_cost_bps"] == pytest.approx(15.0)
    assert diagnostics["cost_margin_bps"] == pytest.approx(0.0)
    assert diagnostics["cost_sensitivity"]["10"] == pytest.approx(0.0005)
