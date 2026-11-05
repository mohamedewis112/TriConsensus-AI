from datetime import UTC, datetime

from triconsensus.account_state import build_account_state
from triconsensus.config import Settings


def test_account_state_is_derived_from_trade_ledger():
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    trades = [
        {
            "status": "closed",
            "pnl_net": "100",
            "closed_at": "2026-10-27T20:00:00+00:00",
        },
        {
            "status": "closed",
            "pnl_net": "-50",
            "closed_at": "2026-10-28T10:30:00+00:00",
        },
        {
            "status": "open",
            "notional_value": "700",
        },
    ]

    state = build_account_state(trades, Settings(), now=now)

    assert state["equity"] == 10050
    assert state["day_start_equity"] == 10100
    assert state["daily_pnl"] == -50
    assert state["peak_equity"] == 10100
    assert state["open_positions"] == 1
    assert state["current_exposure"] == 700
    assert state["correlated_exposure"] == 700
    assert state["losing_streak"] == 1
    assert state["minutes_since_last_loss"] == 90


def test_kill_switch_comes_from_server_configuration():
    state = build_account_state(
        [],
        Settings(emergency_kill_switch=True),
        now=datetime(2026, 10, 28, tzinfo=UTC),
    )
    assert state["kill_switch_active"] is True
