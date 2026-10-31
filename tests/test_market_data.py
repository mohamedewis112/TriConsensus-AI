from triconsensus.market_data import parse_klines, parse_orderbook


def test_bybit_reverse_klines_are_sorted_and_open_candle_dropped():
    rows = [
        ["3000", "3", "4", "2", "3.5", "10", "30"],
        ["2000", "2", "3", "1", "2.5", "9", "20"],
        ["1000", "1", "2", "0.5", "1.5", "8", "10"],
    ]
    out = parse_klines(rows, drop_open_candle=True)
    assert [x["start_ms"] for x in out] == [1000, 2000]
    assert out[-1]["close"] == 2.5


def test_orderbook_imbalance_and_spread_are_deterministic():
    out = parse_orderbook(
        {
            "b": [["100", "3"], ["99", "1"]],
            "a": [["101", "1"], ["102", "1"]],
            "ts": 123,
            "u": 5,
            "seq": 7,
        }
    )
    assert out["imbalance"] == (4 - 2) / 6
    assert out["best_bid"] == 100
    assert out["best_ask"] == 101
    assert out["spread_bps"] > 0
