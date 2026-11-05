from triconsensus.config import Settings
from triconsensus.risk import DeterministicRiskEngine, RiskInput


def base(**overrides):
    data = dict(
        equity=10_000,
        day_start_equity=10_000,
        daily_pnl=0,
        peak_equity=10_000,
        open_positions=0,
        current_exposure=0,
        correlated_exposure=0,
        losing_streak=0,
        minutes_since_last_loss=999999,
        kill_switch_active=False,
        entry=100,
        stop_loss=98,
        take_profit=104,
        spread_bps=2,
        slippage_bps=2,
        realized_volatility_20=0.02,
        market_data_age_ms=500,
        side="LONG",
    )
    data.update(overrides)
    return RiskInput(**data)


def engine():
    return DeterministicRiskEngine(Settings())


def test_valid_trade_is_sized_deterministically():
    d = engine().evaluate(base())
    assert d.approved is True
    assert d.position_size > 0
    assert d.risk_reward == 2.0
    assert d.projected_exposure > 0


def test_stale_market_data_fails_closed():
    d = engine().evaluate(base(market_data_age_ms=999999))
    assert d.approved is False
    assert d.reason == "stale_market_data"


def test_daily_loss_limit_fails_closed():
    d = engine().evaluate(base(daily_pnl=-200))
    assert d.approved is False
    assert d.reason == "daily_loss_limit"


def test_bad_geometry_fails_closed():
    d = engine().evaluate(base(stop_loss=101))
    assert d.approved is False
    assert d.reason == "invalid_long_geometry"


def test_kill_switch_fails_closed():
    d = engine().evaluate(base(kill_switch_active=True))
    assert d.approved is False
    assert d.reason == "kill_switch_active"


def test_losing_streak_limit_fails_closed():
    d = engine().evaluate(base(losing_streak=3))
    assert d.approved is False
    assert d.reason == "losing_streak_limit"


def test_loss_cooldown_fails_closed():
    d = engine().evaluate(base(losing_streak=1, minutes_since_last_loss=15))
    assert d.approved is False
    assert d.reason == "loss_cooldown_active"


def test_slippage_limit_fails_closed():
    d = engine().evaluate(base(slippage_bps=99))
    assert d.approved is False
    assert d.reason == "slippage_too_high"


def test_volatility_limit_fails_closed():
    d = engine().evaluate(base(realized_volatility_20=0.5))
    assert d.approved is False
    assert d.reason == "volatility_too_high"


def test_account_exposure_limit_fails_closed():
    d = engine().evaluate(base(current_exposure=1900))
    assert d.approved is False
    assert d.reason == "max_account_exposure"


def test_correlated_exposure_limit_fails_closed():
    d = engine().evaluate(base(correlated_exposure=1900))
    assert d.approved is False
    assert d.reason == "max_correlated_exposure"
