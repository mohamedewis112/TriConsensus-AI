from __future__ import annotations

from triconsensus.chronos_ws_nervous_system import NervousAccumulator


def test_extracts_microprice_and_trade_flow():
    acc=NervousAccumulator(("BTCUSDT",))
    acc.handle_message({
        "arg":{"channel":"books5","instId":"BTC-USDT-SWAP"},
        "data":[{"ts":"1800000000000","bids":[["70000","8","0","1"],["69999","4","0","1"]],"asks":[["70001","3","0","1"],["70002","4","0","1"]]}],
    })
    acc.handle_message({
        "arg":{"channel":"trades","instId":"BTC-USDT-SWAP"},
        "data":[
            {"ts":"1800000000100","px":"70001","sz":"6","side":"buy"},
            {"ts":"1800000000200","px":"70000","sz":"2","side":"sell"},
        ],
    })
    frame=acc.frame("BTCUSDT",now_ms=1_800_000_000_500)
    assert frame.imbalance_l1 > 0
    assert frame.imbalance_l5 > 0
    assert frame.microprice_offset_bps > 0
    assert frame.trade_imbalance_1s > 0
    assert frame.trade_intensity_1s == 2.0
    assert frame.book_updates == 1
    assert frame.trade_updates == 2
    assert frame.paper_trading_authority is False
    assert frame.live_trading_authority is False


def test_trade_windows_decay_independently():
    acc=NervousAccumulator(("ETHUSDT",))
    acc.handle_message({
        "arg":{"channel":"books5","instId":"ETH-USDT-SWAP"},
        "data":[{"ts":"1800000005000","bids":[["3000","10","0","1"]],"asks":[["3001","10","0","1"]]}],
    })
    acc.handle_message({
        "arg":{"channel":"trades","instId":"ETH-USDT-SWAP"},
        "data":[
            {"ts":"1800000002000","px":"3000","sz":"5","side":"sell"},
            {"ts":"1800000004500","px":"3001","sz":"1","side":"buy"},
        ],
    })
    frame=acc.frame("ETHUSDT",now_ms=1_800_000_005_000)
    assert frame.trade_imbalance_1s == 1.0
    assert frame.trade_imbalance_5s < 0
