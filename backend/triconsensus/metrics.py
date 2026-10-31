from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import mean, pstdev


@dataclass(frozen=True)
class PerformanceMetrics:
    trades: int
    wins: int
    losses: int
    win_rate: float
    net_pnl: float
    profit_factor: float | None
    expectancy: float
    average_win: float
    average_loss: float
    max_drawdown: float
    sharpe: float | None
    sortino: float | None
    max_losing_streak: int


def _max_drawdown(pnls: list[float], starting_equity: float) -> float:
    equity = starting_equity
    peak = starting_equity
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def _max_losing_streak(pnls: list[float]) -> int:
    current = 0
    worst = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            worst = max(worst, current)
        else:
            current = 0
    return worst


def calculate_metrics(
    pnls: list[float],
    *,
    starting_equity: float,
    periods_per_year: int = 365,
) -> PerformanceMetrics:
    if starting_equity <= 0:
        raise ValueError("starting_equity must be positive")

    trades = len(pnls)
    wins_list = [x for x in pnls if x > 0]
    losses_list = [x for x in pnls if x < 0]
    wins = len(wins_list)
    losses = len(losses_list)

    net_pnl = sum(pnls)
    win_rate = wins / trades if trades else 0.0
    average_win = mean(wins_list) if wins_list else 0.0
    average_loss = mean(losses_list) if losses_list else 0.0
    expectancy = net_pnl / trades if trades else 0.0

    gross_profit = sum(wins_list)
    gross_loss = abs(sum(losses_list))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    returns = [p / starting_equity for p in pnls]
    sharpe = None
    if len(returns) > 1:
        sigma = pstdev(returns)
        if sigma > 0:
            sharpe = (mean(returns) / sigma) * math.sqrt(periods_per_year)

    downside = [r for r in returns if r < 0]
    sortino = None
    if downside:
        downside_dev = math.sqrt(sum(r * r for r in downside) / len(downside))
        if downside_dev > 0:
            sortino = (mean(returns) / downside_dev) * math.sqrt(periods_per_year)

    return PerformanceMetrics(
        trades=trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        expectancy=expectancy,
        average_win=average_win,
        average_loss=average_loss,
        max_drawdown=_max_drawdown(pnls, starting_equity),
        sharpe=sharpe,
        sortino=sortino,
        max_losing_streak=_max_losing_streak(pnls),
    )
