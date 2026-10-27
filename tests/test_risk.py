from triconsensus.config import Settings
from triconsensus.risk import DeterministicRiskEngine, RiskInput


def base(**overrides):
    data = dict(
        equity=10_000,
        daily_pnl=0,
        peak_equity=10_000,
        open_positions=0,
        entry=100,
        stop_loss=98,
        take_profit=104,
        spread_bps=2,
        slippage_bps=2,
        market_data_age_ms=500,
        side="LONG",
    )
    data.update(overrides)
    return RiskInput(**data)


def test_valid_trade_is_sized_deterministically():
    d = DeterministicRiskEngine(Settings()).evaluate(base())
    assert d.approved is True
    assert d.position_size > 0
    assert d.risk_reward == 2.0


def test_stale_market_data_fails_closed():
    d = DeterministicRiskEngine(Settings()).evaluate(base(market_data_age_ms=999999))
    assert d.approved is False
    assert d.reason == "stale_market_data"


def test_daily_loss_limit_fails_closed():
    d = DeterministicRiskEngine(Settings()).evaluate(base(daily_pnl=-200))
    assert d.approved is False
    assert d.reason == "daily_loss_limit"


def test_bad_geometry_fails_closed():
    d = DeterministicRiskEngine(Settings()).evaluate(base(stop_loss=101))
    assert d.approved is False
