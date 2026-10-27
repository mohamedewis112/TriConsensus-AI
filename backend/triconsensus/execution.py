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
        if side not in {"LONG", "SHORT"}:
            raise ValueError("invalid side")
        if requested_price <= 0 or quantity <= 0:
            raise ValueError("invalid price/quantity")

        slip = slippage_bps / 10_000.0
        fill = requested_price * (1 + slip if side == "LONG" else 1 - slip)
        notional = fill * quantity
        fee = notional * self.taker_fee_rate
        slippage_cost = abs(fill - requested_price) * quantity

        return SimulatedFill(
            side=side,
            requested_price=requested_price,
            fill_price=fill,
            quantity=quantity,
            fee=fee,
            slippage_cost=slippage_cost,
        )
