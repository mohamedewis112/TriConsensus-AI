import pytest

from triconsensus.setup_replication import compare_setup_reports


def row(key, side, *, mean=0.001, qualified=True):
    return {
        "regime": key,
        "side": side,
        "trades": 80,
        "mean_net_return": mean,
        "profit_factor": 1.4,
        "bootstrap_lcb_95": 0.0002 if mean > 0 else -0.0002,
        "qualified": qualified,
    }


def report(version, end, qualified, ranked):
    return {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "setup_version": version,
        "archive_end_day": end,
        "qualified": qualified,
        "ranked": ranked,
    }


def test_setup_replication_requires_frozen_version():
    first = report("v4.1", "2024-11-30", [], [])
    second = report("v4.2", "2026-08-11", [], [])
    with pytest.raises(ValueError, match="setup versions must match"):
        compare_setup_reports(first, second)


def test_setup_replication_approves_same_setup_regime_side_twice():
    key = "breakout|trend_up|high_vol|momentum_up"
    candidate = row(key, "LONG")
    first = report("v4.1", "2024-11-30", [candidate], [candidate])
    second = report("v4.1", "2026-08-11", [candidate], [candidate])
    policy = compare_setup_reports(first, second)
    assert policy["replicated_count"] == 1
    assert policy["default_action"] == "NO_TRADE"


def test_setup_replication_rejects_disappearing_edge():
    key = "breakout|trend_up|high_vol|momentum_up"
    good = row(key, "LONG")
    bad = row(key, "LONG", mean=-0.001, qualified=False)
    first = report("v4.1", "2024-11-30", [good], [good])
    second = report("v4.1", "2026-08-11", [], [bad])
    policy = compare_setup_reports(first, second)
    assert policy["replicated_count"] == 0
