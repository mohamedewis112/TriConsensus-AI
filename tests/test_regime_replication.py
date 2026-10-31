from triconsensus.regime_replication import compare_regime_reports


def row(regime, side, *, mean=0.001, trades=80, qualified=True, lcb=0.0002):
    return {
        "regime": regime,
        "side": side,
        "mean_net_return": mean,
        "trades": trades,
        "qualified": qualified,
        "bootstrap_lcb_95": lcb,
        "profit_factor": 1.4,
    }


def report(symbol, end, qualified, ranked):
    return {
        "symbol": symbol,
        "timeframe": "15m",
        "archive_end_day": end,
        "qualified": qualified,
        "ranked": ranked,
    }


def test_replication_requires_same_pair_to_pass_both_windows():
    first = report(
        "BTCUSDT",
        "2026-08-11",
        [row("trend_up|mid_vol|momentum_up", "LONG")],
        [],
    )
    second_row = row("trend_up|mid_vol|momentum_up", "LONG")
    second = report(
        "BTCUSDT",
        "2026-05-13",
        [second_row],
        [second_row],
    )
    result = compare_regime_reports(first, second)
    assert result["replicated_count"] == 1
    assert result["replicated_pairs"][0]["side"] == "LONG"


def test_replication_fails_when_edge_disappears():
    first_row = row("trend_up|mid_vol|momentum_up", "LONG")
    weak = row(
        "trend_up|mid_vol|momentum_up",
        "LONG",
        mean=-0.001,
        qualified=False,
        lcb=-0.001,
    )
    first = report("ETHUSDT", "2026-08-11", [first_row], [first_row])
    second = report("ETHUSDT", "2026-05-13", [], [weak])
    result = compare_regime_reports(first, second)
    assert result["replicated_count"] == 0
    assert result["audit"][0]["reason"] == "replication_gate_failed"


def test_replication_rejects_symbol_mismatch():
    first = report("BTCUSDT", "2026-08-11", [], [])
    second = report("ETHUSDT", "2026-05-13", [], [])
    try:
        compare_regime_reports(first, second)
    except ValueError as exc:
        assert "symbols must match" in str(exc)
    else:
        raise AssertionError("expected symbol mismatch to fail")
