from datetime import UTC, datetime

from triconsensus.execution import PaperExecutionEngine
from triconsensus.paper_monitor import (
    duration_seconds,
    evaluate_exit,
    update_excursions,
)


def trade(side="LONG"):
    return {
        "side": side,
        "stop_loss": 95,
        "take_profit_1": 110 if side == "LONG" else 90,
        "actual_entry": 100,
        "position_size": 2,
        "mae": 0,
        "mfe": 0,
    }


def test_long_stop_and_target_are_deterministic():
    assert evaluate_exit(trade("LONG"), 94).reason == "STOP_LOSS"
    assert evaluate_exit(trade("LONG"), 111).reason == "TAKE_PROFIT_1"
    assert evaluate_exit(trade("LONG"), 103).triggered is False


def test_short_stop_and_target_are_deterministic():
    t = trade("SHORT")
    t["stop_loss"] = 105
    assert evaluate_exit(t, 106).reason == "STOP_LOSS"
    assert evaluate_exit(t, 89).reason == "TAKE_PROFIT_1"


def test_exit_slippage_and_fees_reduce_net_pnl():
    engine = PaperExecutionEngine(taker_fee_rate=0.001)
    exit_fill = engine.close_market(
        position_side="LONG",
        requested_price=110,
        quantity=1,
        slippage_bps=10,
    )
    pnl = engine.pnl(
        position_side="LONG",
        entry_fill=100,
        exit_fill=exit_fill.fill_price,
        quantity=1,
        total_fees=0.1 + exit_fill.fee,
    )
    assert exit_fill.fill_price < 110
    assert pnl.net < 10


def test_mae_mfe_update_without_lookahead():
    t = trade("LONG")
    mae, mfe = update_excursions(t, 97)
    assert mae == -6
    assert mfe == 0


def test_duration_seconds():
    closed = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    assert duration_seconds("2026-01-01T00:00:00+00:00", closed) == 60
