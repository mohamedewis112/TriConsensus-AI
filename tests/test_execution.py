from triconsensus.execution import PaperExecutionEngine


def test_long_market_fill_includes_slippage_and_fee():
    fill = PaperExecutionEngine().open_market(
        side="LONG",
        requested_price=100.0,
        quantity=2.0,
        slippage_bps=10.0,
    )
    assert fill.fill_price > 100.0
    assert fill.fee > 0
    assert fill.slippage_cost > 0
