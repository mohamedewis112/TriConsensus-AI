from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable

from .config import Settings


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_account_state(
    trades: Iterable[dict],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict:
    """
    Build risk state only from the immutable/controlled paper-trade ledger.

    The caller is never allowed to supply equity, exposure, streak, or drawdown state.
    BTC and ETH exposure is treated as one correlated crypto bucket in V1.
    """
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    rows = list(trades)
    closed = [row for row in rows if row.get("status") == "closed"]
    opened = [row for row in rows if row.get("status") == "open"]

    closed_asc = sorted(
        closed,
        key=lambda row: _parse_time(row.get("closed_at")) or datetime.min.replace(tzinfo=UTC),
    )

    realized_equity = settings.paper_starting_balance
    peak_equity = settings.paper_starting_balance
    today_pnl = 0.0
    realized_before_today = 0.0

    for row in closed_asc:
        pnl = float(row.get("pnl_net") or 0.0)
        closed_at = _parse_time(row.get("closed_at"))
        realized_equity += pnl
        peak_equity = max(peak_equity, realized_equity)
        if closed_at is not None and closed_at.date() == current_time.date():
            today_pnl += pnl
        else:
            realized_before_today += pnl

    closed_desc = list(reversed(closed_asc))
    losing_streak = 0
    for row in closed_desc:
        pnl = float(row.get("pnl_net") or 0.0)
        if pnl < 0:
            losing_streak += 1
        else:
            break

    minutes_since_last_loss = 999999
    if losing_streak > 0 and closed_desc:
        last_closed_at = _parse_time(closed_desc[0].get("closed_at"))
        if last_closed_at is not None:
            elapsed = current_time - last_closed_at
            minutes_since_last_loss = max(0, int(elapsed.total_seconds() // 60))

    current_exposure = sum(float(row.get("notional_value") or 0.0) for row in opened)

    return {
        "equity": realized_equity,
        "day_start_equity": settings.paper_starting_balance + realized_before_today,
        "daily_pnl": today_pnl,
        "peak_equity": peak_equity,
        "open_positions": len(opened),
        "current_exposure": current_exposure,
        "correlated_exposure": current_exposure,
        "losing_streak": losing_streak,
        "minutes_since_last_loss": minutes_since_last_loss,
        "kill_switch_active": settings.emergency_kill_switch,
        "slippage_bps": settings.paper_slippage_bps,
    }
