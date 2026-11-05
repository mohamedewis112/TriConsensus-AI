import pandas as pd

from triconsensus.features import build_features


def sample_frame(n=80):
    rows = []
    for i in range(n):
        close = 100 + (i * 0.2)
        rows.append(
            {
                "open": close - 0.1,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 100 + i,
            }
        )
    return pd.DataFrame(rows)


def test_feature_engine_includes_quant_and_orderbook_evidence():
    orderbook = {
        "imbalance": 0.25,
        "spread_bps": 2.5,
        "bid_depth": 12.0,
        "ask_depth": 8.0,
    }
    out = build_features(sample_frame(), orderbook=orderbook)
    for key in [
        "rsi_14",
        "macd",
        "atr_14",
        "adx_14",
        "vwap",
        "stoch_k",
        "support_20",
        "resistance_20",
        "atr_normalized_move",
        "orderbook_imbalance",
    ]:
        assert key in out
    assert out["orderbook_imbalance"] == 0.25
    assert out["spread_bps"] == 2.5
