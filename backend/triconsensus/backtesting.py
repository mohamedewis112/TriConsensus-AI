from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestAssumptions:
    fee_rate: float = 0.00055
    slippage_bps: float = 2.0

    def __post_init__(self) -> None:
        if self.fee_rate < 0 or self.slippage_bps < 0:
            raise ValueError("fees/slippage cannot be negative")


@dataclass(frozen=True)
class BacktestTrade:
    side: str
    quantity: float
    raw_entry: float
    raw_exit: float
    effective_entry: float
    effective_exit: float
    gross_pnl: float
    fees: float
    slippage_cost: float
    net_pnl: float


def simulate_round_trip(
    *,
    side: str,
    quantity: float,
    entry_price: float,
    exit_price: float,
    assumptions: BacktestAssumptions,
) -> BacktestTrade:
    if side not in {"LONG", "SHORT"}:
        raise ValueError("invalid side")
    if quantity <= 0 or entry_price <= 0 or exit_price <= 0:
        raise ValueError("quantity and prices must be positive")

    slip = assumptions.slippage_bps / 10_000.0
    if side == "LONG":
        effective_entry = entry_price * (1 + slip)
        effective_exit = exit_price * (1 - slip)
        gross = (effective_exit - effective_entry) * quantity
    else:
        effective_entry = entry_price * (1 - slip)
        effective_exit = exit_price * (1 + slip)
        gross = (effective_entry - effective_exit) * quantity

    fees = (
        effective_entry * quantity * assumptions.fee_rate
        + effective_exit * quantity * assumptions.fee_rate
    )
    ideal_gross = (
        (exit_price - entry_price) * quantity
        if side == "LONG"
        else (entry_price - exit_price) * quantity
    )
    slippage_cost = max(0.0, ideal_gross - gross)
    return BacktestTrade(
        side=side,
        quantity=quantity,
        raw_entry=entry_price,
        raw_exit=exit_price,
        effective_entry=effective_entry,
        effective_exit=effective_exit,
        gross_pnl=gross,
        fees=fees,
        slippage_cost=slippage_cost,
        net_pnl=gross - fees,
    )
