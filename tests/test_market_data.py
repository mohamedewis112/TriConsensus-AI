from triconsensus.market_data import parse_klines


def test_bybit_reverse_klines_are_sorted_and_open_candle_dropped():
    rows = [
        ["3000", "3", "4", "2", "3.5", "10", "30"],
        ["2000", "2", "3", "1", "2.5", "9", "20"],
        ["1000", "1", "2", "0.5", "1.5", "8", "10"],
    ]
    out = parse_klines(rows, drop_open_candle=True)
    assert [x["start_ms"] for x in out] == [1000, 2000]
    assert out[-1]["close"] == 2.5
