import pytest

from triconsensus.backtesting import BacktestAssumptions, simulate_round_trip
from triconsensus.walk_forward import forward_return_targets, walk_forward_splits


def test_walk_forward_never_overlaps_train_with_test():
    splits = walk_forward_splits(100, train_size=40, test_size=10)
    assert splits
    for split in splits:
        assert split.train_end == split.test_start
        assert split.train_end <= split.test_start
        assert split.test_end <= 100


def test_forward_target_leaves_future_tail_unlabeled():
    targets = forward_return_targets([100, 101, 102, 103, 104], horizon=2)
    assert targets[-1] is None
    assert targets[-2] is None
    assert targets[0] == pytest.approx(0.02)


def test_backtest_charges_fees_and_slippage():
    trade = simulate_round_trip(
        side="LONG",
        quantity=1,
        entry_price=100,
        exit_price=110,
        assumptions=BacktestAssumptions(fee_rate=0.001, slippage_bps=10),
    )
    assert trade.net_pnl < 10
    assert trade.fees > 0
    assert trade.slippage_cost > 0
