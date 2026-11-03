from __future__ import annotations

import pytest

from triconsensus.chronos_nervous_system import MarketNervousSystem, NervousSystemFailure
from triconsensus.v6_microstructure import MicrostructureSnapshot


def _snapshot(venue: str, *, received: int, imbalance: float, spread: float):
    return MicrostructureSnapshot(
        venue=venue,
        symbol="ETHUSDT",
        exchange_timestamp_ms=received - 15,
        received_timestamp_ms=received,
        bid_depth=900.0 if venue == "bybit" else 700.0,
        ask_depth=700.0 if venue == "bybit" else 650.0,
        imbalance=imbalance,
        spread_bps=spread,
        sequence=10,
    )


def test_nervous_system_fuses_cross_venue_evidence_into_chronos_state():
    now = 1_800_000_000_000
    system = MarketNervousSystem(max_age_ms=2_000)
    state = system.build_state(
        [
            _snapshot("bybit", received=now - 100, imbalance=0.30, spread=1.5),
            _snapshot("binance", received=now - 180, imbalance=0.20, spread=1.2),
        ],
        symbol="ETHUSDT",
        as_of_ms=now,
        mid_price=3200.0,
        returns={"return_1m": 0.002, "return_5m": 0.006, "realized_volatility": 0.02},
        derivatives={
            "funding_rate": 0.0001,
            "open_interest_change": 0.02,
            "liquidation_imbalance": 0.1,
            "cross_venue_basis_bps": 2.0,
            "crowding_score": 0.2,
        },
        alternative={"stablecoin_flow_z": 0.8, "dealer_gamma_z": -0.3},
    )
    assert state.bid_depth == 1600.0
    assert state.ask_depth == 1350.0
    assert 0.20 < state.order_flow_imbalance < 0.30
    assert 1.2 < state.spread_bps < 1.5
    assert state.cross_venue_latency_ms == 80.0
    assert state.stablecoin_flow_z == 0.8


def test_nervous_system_fails_closed_on_single_venue_or_stale_data():
    now = 1_800_000_000_000
    system = MarketNervousSystem(max_age_ms=500)
    with pytest.raises(NervousSystemFailure):
        system.fuse_microstructure(
            [_snapshot("bybit", received=now - 100, imbalance=0.2, spread=1.5)],
            as_of_ms=now,
        )
    with pytest.raises(NervousSystemFailure):
        system.fuse_microstructure(
            [
                _snapshot("bybit", received=now - 900, imbalance=0.2, spread=1.5),
                _snapshot("binance", received=now - 850, imbalance=0.1, spread=1.2),
            ],
            as_of_ms=now,
        )
