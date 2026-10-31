import inspect

import pytest

import triconsensus.research_cycle as research_cycle


def test_frozen_v51_thresholds_are_exact():
    assert research_cycle.FROZEN_THRESHOLDS["BTCUSDT"]["low"] == pytest.approx(
        -0.019545157125130854,
        rel=0,
        abs=1e-15,
    )
    assert research_cycle.FROZEN_THRESHOLDS["BTCUSDT"]["high"] == pytest.approx(
        0.020643022165240422,
        rel=0,
        abs=1e-15,
    )
    assert research_cycle.FROZEN_THRESHOLDS["ETHUSDT"]["low"] == pytest.approx(
        -0.01949802950955428,
        rel=0,
        abs=1e-15,
    )
    assert research_cycle.FROZEN_THRESHOLDS["ETHUSDT"]["high"] == pytest.approx(
        0.020743615937876794,
        rel=0,
        abs=1e-15,
    )


def test_live_research_cycle_has_no_execution_or_persistence_authority():
    source = inspect.getsource(research_cycle)
    assert "PaperExecutionEngine" not in source
    assert "SupabaseRepository" not in source
    assert ".insert(" not in source
    assert ".update(" not in source


def test_research_cycle_reuses_frozen_protocol_constants():
    assert research_cycle.CONTEXT_LAG_MINUTES == 15
    assert research_cycle.HORIZON_MINUTES == 180
    assert research_cycle.ROUND_TRIP_RESEARCH_COST == pytest.approx(0.0015)
    assert research_cycle.RELATIONSHIP == "negative"
