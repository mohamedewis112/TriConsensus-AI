from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .chronos import MarketState
from .v6_microstructure import MicrostructureSnapshot


class NervousSystemFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class FusedMicrostructure:
    venues: tuple[str, ...]
    weighted_imbalance: float
    weighted_spread_bps: float
    bid_depth: float
    ask_depth: float
    max_age_ms: int
    receive_skew_ms: int


class MarketNervousSystem:
    """Fuse V6 live microstructure into the V8 CHRONOS decision-time state.

    The adapter is deliberately read-only.  It does not persist, execute orders,
    mutate V5.1, or create MARL rewards.
    """

    def __init__(self, *, max_age_ms: int = 2_500, require_cross_venue: bool = True):
        if max_age_ms <= 0:
            raise ValueError("max_age_ms must be positive")
        self.max_age_ms = max_age_ms
        self.require_cross_venue = require_cross_venue

    def fuse_microstructure(
        self,
        snapshots: Sequence[MicrostructureSnapshot],
        *,
        as_of_ms: int,
    ) -> FusedMicrostructure:
        if not snapshots:
            raise NervousSystemFailure("no microstructure snapshots")
        latest: dict[str, MicrostructureSnapshot] = {}
        for snapshot in snapshots:
            current = latest.get(snapshot.venue)
            if current is None or snapshot.received_timestamp_ms > current.received_timestamp_ms:
                latest[snapshot.venue] = snapshot
        if self.require_cross_venue and len(latest) < 2:
            raise NervousSystemFailure("CHRONOS requires cross-venue microstructure")

        rows = tuple(latest.values())
        ages = [max(0, as_of_ms - row.received_timestamp_ms) for row in rows]
        if max(ages) > self.max_age_ms:
            raise NervousSystemFailure("stale microstructure evidence")

        freshness = [1.0 / (1.0 + age / 1_000.0) for age in ages]
        weight_sum = sum(freshness)
        imbalance = sum(w * row.imbalance for w, row in zip(freshness, rows)) / weight_sum
        spread = sum(w * row.spread_bps for w, row in zip(freshness, rows)) / weight_sum
        receive_times = [row.received_timestamp_ms for row in rows]
        return FusedMicrostructure(
            venues=tuple(sorted(latest)),
            weighted_imbalance=float(imbalance),
            weighted_spread_bps=float(spread),
            bid_depth=float(sum(row.bid_depth for row in rows)),
            ask_depth=float(sum(row.ask_depth for row in rows)),
            max_age_ms=max(ages),
            receive_skew_ms=max(receive_times) - min(receive_times),
        )

    def build_state(
        self,
        snapshots: Sequence[MicrostructureSnapshot],
        *,
        symbol: str,
        as_of_ms: int,
        mid_price: float,
        returns: Mapping[str, float] | None = None,
        derivatives: Mapping[str, float] | None = None,
        alternative: Mapping[str, float] | None = None,
    ) -> MarketState:
        micro = self.fuse_microstructure(snapshots, as_of_ms=as_of_ms)
        returns = returns or {}
        derivatives = derivatives or {}
        alternative = alternative or {}
        return MarketState(
            symbol=symbol,
            timestamp_ms=as_of_ms,
            mid_price=mid_price,
            return_1m=float(returns.get("return_1m", 0.0)),
            return_5m=float(returns.get("return_5m", 0.0)),
            realized_volatility=max(0.0, float(returns.get("realized_volatility", 0.0))),
            bid_depth=micro.bid_depth,
            ask_depth=micro.ask_depth,
            order_flow_imbalance=max(-1.0, min(1.0, micro.weighted_imbalance)),
            spread_bps=max(0.0, micro.weighted_spread_bps),
            funding_rate=float(derivatives.get("funding_rate", 0.0)),
            open_interest_change=float(derivatives.get("open_interest_change", 0.0)),
            liquidation_imbalance=max(
                -1.0, min(1.0, float(derivatives.get("liquidation_imbalance", 0.0)))
            ),
            cross_venue_basis_bps=float(derivatives.get("cross_venue_basis_bps", 0.0)),
            cross_venue_latency_ms=float(micro.receive_skew_ms),
            stablecoin_flow_z=float(alternative.get("stablecoin_flow_z", 0.0)),
            dealer_gamma_z=float(alternative.get("dealer_gamma_z", 0.0)),
            mempool_pressure_z=float(alternative.get("mempool_pressure_z", 0.0)),
            crowding_score=max(
                -1.0, min(1.0, float(derivatives.get("crowding_score", 0.0)))
            ),
        )
