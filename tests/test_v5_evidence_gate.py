from triconsensus.v5_evidence_gate import (
    cross_symbol_replication,
    replicate_symbol,
)


def row(feature, horizon, ic, spread=0.002):
    return {
        "feature": feature,
        "horizon": horizon,
        "mean_rank_ic": ic,
        "mean_cost_adjusted_decile_spread": spread,
        "qualified": True,
    }


def report(symbol, rows):
    return {
        "symbol": symbol,
        "timeframe": "15m",
        "pure_qualified": rows,
    }


def test_symbol_replication_requires_same_sign_and_key():
    discovery = report(
        "BTCUSDT",
        [
            row("global_long_short_change_1h", 12, -0.30),
            row("oi_change_1h", 12, 0.10),
        ],
    )
    replication = report(
        "BTCUSDT",
        [
            row("global_long_short_change_1h", 12, -0.25),
            row("oi_change_1h", 12, -0.08),
        ],
    )
    rows = replicate_symbol(discovery, replication)
    assert len(rows) == 1
    assert rows[0].feature == "global_long_short_change_1h"
    assert rows[0].sign == "negative"
    assert rows[0].windows == 2


def test_cross_symbol_gate_requires_both_symbols():
    btc_d = report(
        "BTCUSDT",
        [row("global_long_short_change_1h", 12, -0.35, 0.010)],
    )
    btc_r = report(
        "BTCUSDT",
        [row("global_long_short_change_1h", 12, -0.30, 0.011)],
    )
    eth_d = report(
        "ETHUSDT",
        [row("global_long_short_change_1h", 12, -0.40, 0.012)],
    )
    eth_r = report(
        "ETHUSDT",
        [row("global_long_short_change_1h", 12, -0.38, 0.013)],
    )

    result = cross_symbol_replication(
        {
            "BTCUSDT": (btc_d, btc_r),
            "ETHUSDT": (eth_d, eth_r),
        }
    )
    assert result["cross_symbol_count"] == 1
    candidate = result["cross_symbol_replicated"][0]
    assert candidate["feature"] == "global_long_short_change_1h"
    assert candidate["horizon"] == 12
    assert candidate["sign"] == "negative"
    assert candidate["worst_abs_rank_ic"] == 0.30
    assert candidate["worst_cost_adjusted_spread"] == 0.010
    assert candidate["windows"] == 4


def test_cross_symbol_gate_is_fail_closed_on_sign_disagreement():
    btc = report(
        "BTCUSDT",
        [row("x", 12, 0.10)],
    )
    eth_d = report(
        "ETHUSDT",
        [row("x", 12, -0.10)],
    )
    eth_r = report(
        "ETHUSDT",
        [row("x", 12, -0.12)],
    )
    result = cross_symbol_replication(
        {
            "BTCUSDT": (btc, btc),
            "ETHUSDT": (eth_d, eth_r),
        }
    )
    assert result["cross_symbol_count"] == 0
