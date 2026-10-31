from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SimulatedFill:
    side: str
    requested_price: float
    fill_price: float
    quantity: float
    fee: float
    slippage_cost: float


@dataclass(frozen=True)
class PaperPnL:
    gross: float
    fees: float
    net: float


class PaperExecutionEngine:
    def __init__(self, *, taker_fee_rate: float = 0.00055) -> None:
        self.taker_fee_rate = taker_fee_rate

    def open_market(
        self,
        *,
        side: str,
        requested_price: float,
        quantity: float,
        slippage_bps: float,
    ) -> SimulatedFill:
        self._validate(side, requested_price, quantity, slippage_bps)
        slip = slippage_bps / 10_000.0
        fill = requested_price * (1 + slip if side == "LONG" else 1 - slip)
        return self._fill(side, requested_price, fill, quantity)

    def close_market(
        self,
        *,
        position_side: str,
        requested_price: float,
        quantity: float,
        slippage_bps: float,
    ) -> SimulatedFill:
        self._validate(position_side, requested_price, quantity, slippage_bps)
        slip = slippage_bps / 10_000.0
        # Closing LONG sells, so adverse slippage lowers the exit.
        # Closing SHORT buys, so adverse slippage raises the exit.
        fill = requested_price * (1 - slip if position_side == "LONG" else 1 + slip)
        return self._fill(position_side, requested_price, fill, quantity)

    @staticmethod
    def pnl(
        *,
        position_side: str,
        entry_fill: float,
        exit_fill: float,
        quantity: float,
        total_fees: float,
    ) -> PaperPnL:
        if position_side == "LONG":
            gross = (exit_fill - entry_fill) * quantity
        elif position_side == "SHORT":
            gross = (entry_fill - exit_fill) * quantity
        else:
            raise ValueError("invalid side")
        return PaperPnL(gross=gross, fees=total_fees, net=gross - total_fees)

    def _fill(
        self,
        side: str,
        requested_price: float,
        fill_price: float,
        quantity: float,
    ) -> SimulatedFill:
        notional = fill_price * quantity
        fee = notional * self.taker_fee_rate
        slippage_cost = abs(fill_price - requested_price) * quantity
        return SimulatedFill(
            side=side,
            requested_price=requested_price,
            fill_price=fill_price,
            quantity=quantity,
            fee=fee,
            slippage_cost=slippage_cost,
        )

    @staticmethod
    def _validate(
        side: str,
        requested_price: float,
        quantity: float,
        slippage_bps: float,
    ) -> None:
        if side not in {"LONG", "SHORT"}:
            raise ValueError("invalid side")
        if requested_price <= 0 or quantity <= 0:
            raise ValueError("invalid price/quantity")
        if slippage_bps < 0:
            raise ValueError("slippage cannot be negative")
