from __future__ import annotations

from triconsensus.chronos_learning import ProbabilisticTransitionEnsemble
from triconsensus.chronos_replay import (
    minute_bucket,
    states_from_real_replay,
)


def _fixture(minutes: int = 260):
    base = 1_800_000_000_000
    micro = []
    candles = []
    price = 3200.0
    for i in range(minutes):
        ts = base + i * 60_000
        ofi = ((i % 17) - 8) / 12.0
        for venue, lag, spread in (("bybit", 100, 1.4), ("binance", 180, 1.2)):
            micro.append(
                {
                    "symbol": "ETH/USDT",
                    "venue": venue,
                    "received_timestamp_ms": ts + lag,
                    "exchange_timestamp_ms": ts + lag - 10,
                    "bid_depth": 1000.0 + 80.0 * ofi,
                    "ask_depth": 950.0 - 80.0 * ofi,
                    "imbalance": ofi * (1.0 if venue == "bybit" else 0.92),
                    "spread_bps": spread,
                }
            )
        price *= 1.0 + 0.00012 * ofi
        candles.append(
            {
                "start_ms": ts,
                "open": price * 0.9999,
                "high": price * 1.0004,
                "low": price * 0.9996,
                "close": price,
                "volume": 1000.0,
                "turnover": 0.0,
            }
        )
    return micro, candles


def test_minute_bucket_is_stable():
    assert minute_bucket(1_800_000_012_345) == 1_800_000_000_000


def test_real_replay_builder_requires_and_fuses_both_venues():
    micro, candles = _fixture()
    states, coverage = states_from_real_replay(
        symbol="ETHUSDT",
        microstructure_rows=micro,
        candles_1m=candles,
    )
    assert coverage.cross_venue_minutes == len(candles)
    assert coverage.aligned_states == len(candles)
    assert coverage.alignment_ratio == 1.0
    assert len(states) == len(candles)
    assert states[-1].cross_venue_latency_ms == 80.0
    assert states[-1].bid_depth > 0
    assert -1.0 <= states[-1].order_flow_imbalance <= 1.0


def test_real_replay_output_can_train_v81_without_synthetic_labels():
    micro, candles = _fixture(320)
    states, coverage = states_from_real_replay(
        symbol="ETHUSDT",
        microstructure_rows=micro,
        candles_1m=candles,
    )
    from triconsensus.chronos_learning import transitions_from_states

    transitions = transitions_from_states(states)
    model = ProbabilisticTransitionEnsemble(
        heads=5,
        minimum_samples=180,
        seed=55,
    )
    report = model.fit(transitions)
    assert len(transitions) >= 180
    assert report.evaluation_rows >= 20
    assert model.predict(states[-1]).paper_trading_authority is False
