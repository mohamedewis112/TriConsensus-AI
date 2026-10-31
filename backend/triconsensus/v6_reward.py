from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from math import prod
from typing import Iterable

from .v6_models import AssetRollingStats, PortfolioState, RewardMemoryRecord


def reward_value(
    *,
    net_return: float,
    max_drawdown: float,
    drawdown_penalty: float,
) -> float:
    if drawdown_penalty < 0:
        raise ValueError("drawdown_penalty cannot be negative")
    if not 0 <= max_drawdown <= 1:
        raise ValueError("max_drawdown must be inside [0,1]")
    return float(net_return) - (float(drawdown_penalty) * float(max_drawdown))


def _rolling_stats(symbol: str, rows: list[RewardMemoryRecord]) -> AssetRollingStats:
    selected = [row for row in rows if row.symbol == symbol][-40:]
    returns = [float(row.net_return) for row in selected]
    hits = [value > 0 for value in returns]

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    losing_streak = 0
    current_streak = 0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
        if value < 0:
            current_streak += 1
            losing_streak = max(losing_streak, current_streak)
        else:
            current_streak = 0

    compounded = prod(1.0 + value for value in returns) - 1.0 if returns else 0.0
    return AssetRollingStats(
        symbol=symbol,
        sample_size=len(returns),
        hit_rate=(sum(hits) / len(hits)) if hits else 0.0,
        mean_net_return=(sum(returns) / len(returns)) if returns else 0.0,
        cumulative_net_return=compounded,
        max_drawdown=max_drawdown,
        losing_streak=losing_streak,
    )


def build_portfolio_state(
    records: Iterable[RewardMemoryRecord],
    *,
    window: int = 40,
    minimum_warmup_trades_per_asset: int = 20,
    as_of: datetime | None = None,
) -> PortfolioState:
    if not 20 <= window <= 40:
        raise ValueError("V6 rolling reward window must be within [20,40]")
    rows = list(records)[-window * 2 :]
    by_symbol: dict[str, list[RewardMemoryRecord]] = defaultdict(list)
    for row in rows:
        by_symbol[row.symbol].append(row)

    assets = {
        "BTC/USDT": _rolling_stats("BTC/USDT", by_symbol["BTC/USDT"]),
        "ETH/USDT": _rolling_stats("ETH/USDT", by_symbol["ETH/USDT"]),
    }

    portfolio_returns = [float(row.net_return) for row in rows[-window:]]
    equity = 1.0
    peak = 1.0
    portfolio_drawdown = 0.0
    for value in portfolio_returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0:
            portfolio_drawdown = max(portfolio_drawdown, (peak - equity) / peak)

    warmup_complete = all(
        stats.sample_size >= minimum_warmup_trades_per_asset
        for stats in assets.values()
    )
    return PortfolioState(
        as_of=as_of or datetime.now(UTC),
        assets=assets,
        portfolio_drawdown=portfolio_drawdown,
        recent_trade_count=sum(stats.sample_size for stats in assets.values()),
        warmup_complete=warmup_complete,
    )
