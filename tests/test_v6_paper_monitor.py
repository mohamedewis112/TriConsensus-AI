from datetime import UTC, datetime, timedelta

import pytest

from triconsensus.paper_monitor import (
    evaluate_exit,
    horizon_expired,
    project_live_levels,
    update_excursions,
)
from triconsensus.v6_models import DynamicRiskAction


def trade(**overrides):
    row = {
        "side": "LONG",
        "actual_entry": 100.0,
        "position_size": 1.0,
        "stop_loss": 98.0,
        "take_profit_1": 104.0,
        "mae": 0.0,
        "mfe": 0.0,
    }
    row.update(overrides)
    return row


def test_trailing_profit_activates_only_after_one_r_mfe():
    row = trade()
    _, mfe = update_excursions(row, 103.0)
    assert mfe == pytest.approx(3.0)

    signal = evaluate_exit(
        row,
        101.0,
        mfe=mfe,
        trailing_lock_fraction=0.35,
    )
    assert signal.triggered is True
    assert signal.reason == "TRAILING_PROFIT"


def test_trailing_profit_does_not_activate_before_one_r():
    row = trade()
    signal = evaluate_exit(
        row,
        100.2,
        mfe=1.5,
        trailing_lock_fraction=0.50,
    )
    assert signal.triggered is False


def test_fixed_stop_still_has_priority_over_trailing():
    row = trade()
    signal = evaluate_exit(
        row,
        97.9,
        mfe=5.0,
        trailing_lock_fraction=0.50,
    )
    assert signal.triggered is True
    assert signal.reason == "STOP_LOSS"


def test_v6_horizon_closes_at_180_minutes():
    now = datetime(2026, 10, 28, 16, 15, tzinfo=UTC)
    opened = (now - timedelta(minutes=180)).isoformat()
    assert horizon_expired(opened, now, horizon_minutes=180) is True


def test_v6_horizon_does_not_close_early():
    now = datetime(2026, 10, 28, 16, 14, 59, tzinfo=UTC)
    opened = (now - timedelta(minutes=179, seconds=59)).isoformat()
    assert horizon_expired(opened, now, horizon_minutes=180) is False


def live_action(*, stop_atr: float, rr: float) -> DynamicRiskAction:
    return DynamicRiskAction(
        stop_atr_multiplier=stop_atr,
        reward_to_risk_multiplier=rr,
        trailing_lock_fraction=0.35,
        policy_version="test",
        reason="test",
        shadow_mode=False,
    )


def test_live_long_projection_can_tighten_but_never_widen_loss():
    row = trade(stop_loss=98.0, take_profit_1=104.0)
    stop, tp = project_live_levels(
        row,
        atr=1.0,
        action=live_action(stop_atr=1.0, rr=1.0),
        minimum_risk_reward=1.5,
    )
    assert stop == pytest.approx(99.0)
    assert tp == pytest.approx(101.5)

    wider_stop, farther_tp = project_live_levels(
        {**row, "stop_loss": stop, "take_profit_1": tp},
        atr=5.0,
        action=live_action(stop_atr=2.0, rr=1.5),
        minimum_risk_reward=1.5,
    )
    assert wider_stop == pytest.approx(99.0)
    assert farther_tp == pytest.approx(101.5)


def test_live_short_projection_can_tighten_but_never_widen_loss():
    row = trade(
        side="SHORT",
        stop_loss=102.0,
        take_profit_1=96.0,
    )
    stop, tp = project_live_levels(
        row,
        atr=1.0,
        action=live_action(stop_atr=1.0, rr=1.0),
        minimum_risk_reward=1.5,
    )
    assert stop == pytest.approx(101.0)
    assert tp == pytest.approx(98.5)

    wider_stop, farther_tp = project_live_levels(
        {**row, "stop_loss": stop, "take_profit_1": tp},
        atr=5.0,
        action=live_action(stop_atr=2.0, rr=1.5),
        minimum_risk_reward=1.5,
    )
    assert wider_stop == pytest.approx(101.0)
    assert farther_tp == pytest.approx(98.5)
