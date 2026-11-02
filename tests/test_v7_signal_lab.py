from __future__ import annotations

from dataclasses import asdict

import pytest

from triconsensus.regimes import RegimeState
from triconsensus.v7_signal_lab import (
    build_v7_price_frame,
    generate_microstructure_candidate,
    generate_price_candidates,
    multi_horizon_outcome,
)
from triconsensus.v7_validation import purged_walk_forward_splits


def _candles(n: int = 220) -> list[dict]:
    rows = []
    price = 100.0
    for i in range(n):
        drift = 0.18 if i < n * 0.55 else -0.08
        wiggle = ((i % 7) - 3) * 0.03
        open_ = price
        close = max(1.0, open_ + drift + wiggle)
        high = max(open_, close) + 0.22
        low = min(open_, close) - 0.22
        rows.append(
            {
                "start_ms": i * 15 * 60 * 1000,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0 + (i % 11) * 25.0,
                "turnover": 0.0,
            }
        )
        price = close
    return rows


def test_v7_price_features_are_causal_when_future_is_mutated():
    base = _candles(220)
    short = build_v7_price_frame(base[:170])
    mutated = [dict(row) for row in base]
    for row in mutated[170:]:
        row["open"] *= 10
        row["high"] *= 10
        row["low"] *= 10
        row["close"] *= 10
        row["volume"] *= 100
    long = build_v7_price_frame(mutated)

    keys = [
        "ema_20",
        "ema_50",
        "rsi_14",
        "atr_14",
        "adx_14",
        "roc_10",
        "realized_vol_20",
        "volume_z_20",
        "bb_z_20",
        "stoch_k",
    ]
    for key in keys:
        assert float(short.iloc[169][key]) == pytest.approx(float(long.iloc[169][key]))


def test_router_keeps_trend_and_range_edges_independent():
    trend = RegimeState(
        trend="trend_up",
        volatility="mid_vol",
        momentum="momentum_up",
        key="trend_up|mid_vol|momentum_up",
        trend_strength_atr=1.2,
        realized_volatility=0.02,
        adx=28.0,
    )
    features = {
        "close": 110.0,
        "ema_20": 108.0,
        "ema_50": 104.0,
        "atr_14": 2.0,
        "adx_14": 28.0,
        "roc_10": 0.03,
        "macd_hist": 1.0,
        "volume_z_20": 1.2,
        "breakout_up_distance_atr": 0.4,
        "breakout_down_distance_atr": -3.0,
        "return_4": 0.01,
        "return_16": 0.04,
        "rsi_14": 62.0,
        "stoch_k": 70.0,
        "bb_z_20": 1.0,
        "atr_normalized_move": 0.5,
    }
    candidates = generate_price_candidates(features, trend)
    assert any(c.species == "trend_breakout" and c.side == "LONG" for c in candidates)
    assert all(c.research_mode == "historical_research_only" for c in candidates)


def test_mean_reversion_can_use_crowding_confirmation_without_requiring_it():
    range_state = RegimeState(
        trend="range",
        volatility="mid_vol",
        momentum="momentum_mixed",
        key="range|mid_vol|momentum_mixed",
        trend_strength_atr=0.1,
        realized_volatility=0.01,
        adx=14.0,
    )
    features = {
        "close": 100.0,
        "ema_20": 100.0,
        "ema_50": 100.1,
        "atr_14": 1.0,
        "adx_14": 14.0,
        "roc_10": -0.01,
        "macd_hist": -0.1,
        "volume_z_20": 0.0,
        "breakout_up_distance_atr": -2.0,
        "breakout_down_distance_atr": -0.1,
        "return_4": -0.01,
        "return_16": 0.0,
        "rsi_14": 28.0,
        "stoch_k": 12.0,
        "bb_z_20": -1.8,
        "atr_normalized_move": -0.8,
    }
    base = generate_price_candidates(features, range_state)
    crowded = generate_price_candidates(
        features,
        range_state,
        derivatives={
            "funding_rate": -0.0002,
            "long_short_ratio": 0.80,
            "oi_change_4h": -0.04,
        },
    )
    base_signal = next(c for c in base if c.species == "mean_reversion_range")
    crowded_signal = next(c for c in crowded if c.species == "mean_reversion_range")
    assert base_signal.side == "LONG"
    assert not base_signal.crowding_confirmed
    assert crowded_signal.crowding_confirmed
    assert crowded_signal.confidence > base_signal.confidence


def test_microstructure_edge_is_cross_venue_and_shadow_only():
    candidate = generate_microstructure_candidate(
        symbol="BTCUSDT",
        bybit={"imbalance": 0.30, "spread_bps": 1.5},
        binance={"imbalance": 0.24, "spread_bps": 1.2},
        regime_key="trend_up|mid_vol|momentum_up",
    )
    assert candidate is not None
    assert candidate.side == "LONG"
    assert candidate.species == "microstructure_orderflow"
    assert candidate.research_mode == "live_shadow_only"

    disagreement = generate_microstructure_candidate(
        symbol="BTCUSDT",
        bybit={"imbalance": 0.30, "spread_bps": 1.5},
        binance={"imbalance": -0.24, "spread_bps": 1.2},
        regime_key="range|mid_vol|momentum_mixed",
    )
    assert disagreement is None


def test_multi_horizon_outcome_charges_cost_and_tracks_mae_mfe():
    candles = []
    for i in range(20):
        close = 100.0 + i
        candles.append(
            {
                "start_ms": i * 900_000,
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000.0,
                "turnover": 0.0,
            }
        )
    outcome = multi_horizon_outcome(candles, index=2, side="LONG", round_trip_cost=0.0015)
    assert outcome.net_return_30m < outcome.net_return_60m < outcome.net_return_180m
    assert outcome.mfe_180m > 0
    assert outcome.mae_180m > -0.01


def test_time_purged_walk_forward_has_full_180m_gap():
    timestamps = [i * 15 * 60 * 1000 for i in range(800)]
    splits = purged_walk_forward_splits(
        timestamps,
        train_size=300,
        test_size=80,
        step_size=80,
        embargo_ms=180 * 60 * 1000,
    )
    assert len(splits) >= 3
    for split in splits:
        gap = timestamps[split.test_start] - timestamps[split.train_end - 1]
        assert gap > 180 * 60 * 1000
        assert split.train_end <= split.test_start
