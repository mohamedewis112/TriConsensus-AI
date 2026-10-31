from triconsensus.metrics import calculate_metrics


def test_metrics_do_not_confuse_win_rate_with_profitability():
    m = calculate_metrics([10, 10, 10, 10, -100], starting_equity=1000)
    assert m.win_rate == 0.8
    assert m.net_pnl == -60
    assert m.expectancy == -12
    assert m.profit_factor == 0.4


def test_losing_streak_and_drawdown_are_reported():
    m = calculate_metrics([20, -10, -15, -5, 30], starting_equity=1000)
    assert m.max_losing_streak == 3
    assert m.max_drawdown > 0
