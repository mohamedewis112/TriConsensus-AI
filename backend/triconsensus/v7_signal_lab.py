from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math
from typing import Mapping

import numpy as np
import pandas as pd

from .regime_research import build_regime_feature_frame
from .regimes import RegimeState


V7_SIGNAL_LAB_VERSION = "v7.0-signal-lab-research-1"
V7_ALLOWED_SPECIES = (
    "trend_breakout",
    "mean_reversion_range",
    "microstructure_orderflow",
)
V7_HORIZON_BARS = {"30m": 2, "60m": 4, "180m": 12}


@dataclass(frozen=True)
class V7Candidate:
    species: str
    side: str
    confidence: float
    reason: str
    regime_key: str
    research_mode: str
    crowding_confirmed: bool = False


@dataclass(frozen=True)
class V7Outcome:
    net_return_30m: float
    net_return_60m: float
    net_return_180m: float
    mae_180m: float
    mfe_180m: float


def _finite(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def build_v7_price_frame(candles: list[dict]) -> pd.DataFrame:
    """Causal price features only; every rolling boundary uses current/past bars."""
    frame = build_regime_feature_frame(candles)
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]

    frame["return_1"] = close.pct_change(1)
    frame["return_4"] = close.pct_change(4)
    frame["return_16"] = close.pct_change(16)
    frame["ema_gap_atr"] = (
        (frame["ema_20"] - frame["ema_50"])
        / frame["atr_14"].replace(0, np.nan)
    )

    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std().replace(0, np.nan)
    frame["bb_z_20"] = (close - bb_mid) / bb_std

    lowest_14 = low.rolling(14).min()
    highest_14 = high.rolling(14).max()
    range_14 = (highest_14 - lowest_14).replace(0, np.nan)
    frame["stoch_k"] = 100.0 * (close - lowest_14) / range_14

    frame["breakout_up_distance_atr"] = (
        (close - frame["resistance_20"]) / frame["atr_14"].replace(0, np.nan)
    )
    frame["breakout_down_distance_atr"] = (
        (frame["support_20"] - close) / frame["atr_14"].replace(0, np.nan)
    )
    return frame


def route_species(regime: RegimeState, features: Mapping[str, object]) -> tuple[str, ...]:
    """Choose which independent edge families are allowed to produce candidates."""
    allowed: list[str] = []
    adx = _finite(features.get("adx_14"))
    volume_z = _finite(features.get("volume_z_20"))

    if regime.trend in {"trend_up", "trend_down"} or (
        adx >= 18 and volume_z >= 0.5
    ):
        allowed.append("trend_breakout")

    if regime.trend == "range" or adx < 20:
        allowed.append("mean_reversion_range")

    return tuple(allowed)


def _trend_breakout_candidate(
    features: Mapping[str, object],
    regime: RegimeState,
) -> V7Candidate | None:
    close = _finite(features.get("close"))
    ema20 = _finite(features.get("ema_20"))
    ema50 = _finite(features.get("ema_50"))
    adx = _finite(features.get("adx_14"))
    roc10 = _finite(features.get("roc_10"))
    macd_hist = _finite(features.get("macd_hist"))
    volume_z = _finite(features.get("volume_z_20"))
    breakout_up = _finite(features.get("breakout_up_distance_atr"))
    breakout_down = _finite(features.get("breakout_down_distance_atr"))
    ret4 = _finite(features.get("return_4"))
    ret16 = _finite(features.get("return_16"))

    long_votes = sum(
        (
            ema20 > ema50,
            close > ema20,
            roc10 > 0,
            macd_hist > 0,
            ret4 > 0,
            ret16 > 0,
            breakout_up > 0,
        )
    )
    short_votes = sum(
        (
            ema20 < ema50,
            close < ema20,
            roc10 < 0,
            macd_hist < 0,
            ret4 < 0,
            ret16 < 0,
            breakout_down > 0,
        )
    )
    breakout_bonus = _clip(max(breakout_up, breakout_down) / 1.5)
    quality = _clip((adx - 15.0) / 25.0)
    volume_quality = _clip((volume_z + 0.5) / 3.0)

    if long_votes >= 5 and regime.trend != "trend_down":
        confidence = _clip(
            0.35 + 0.05 * long_votes + 0.15 * quality
            + 0.10 * volume_quality + 0.10 * breakout_bonus
        )
        return V7Candidate(
            species="trend_breakout",
            side="LONG",
            confidence=confidence,
            reason="trend_momentum_breakout_alignment",
            regime_key=regime.key,
            research_mode="historical_research_only",
        )

    if short_votes >= 5 and regime.trend != "trend_up":
        confidence = _clip(
            0.35 + 0.05 * short_votes + 0.15 * quality
            + 0.10 * volume_quality + 0.10 * breakout_bonus
        )
        return V7Candidate(
            species="trend_breakout",
            side="SHORT",
            confidence=confidence,
            reason="trend_momentum_breakout_alignment",
            regime_key=regime.key,
            research_mode="historical_research_only",
        )
    return None


def _mean_reversion_candidate(
    features: Mapping[str, object],
    regime: RegimeState,
    derivatives: Mapping[str, object] | None,
) -> V7Candidate | None:
    rsi = _finite(features.get("rsi_14"), 50.0)
    stoch = _finite(features.get("stoch_k"), 50.0)
    bb_z = _finite(features.get("bb_z_20"))
    atr_move = _finite(features.get("atr_normalized_move"))
    adx = _finite(features.get("adx_14"))
    support_dist = _finite(features.get("breakout_down_distance_atr"))
    resistance_dist = _finite(features.get("breakout_up_distance_atr"))

    long_extreme = (
        (bb_z <= -1.4 and rsi <= 38)
        or (stoch <= 20 and atr_move <= -0.5)
        or (support_dist >= -0.20 and rsi <= 32)
    )
    short_extreme = (
        (bb_z >= 1.4 and rsi >= 62)
        or (stoch >= 80 and atr_move >= 0.5)
        or (resistance_dist >= -0.20 and rsi >= 68)
    )

    if regime.trend != "range" and adx >= 22:
        return None

    crowding_long = False
    crowding_short = False
    if derivatives:
        funding = _finite(derivatives.get("funding_rate"))
        ratio = _finite(derivatives.get("long_short_ratio"), 1.0)
        oi_change = _finite(derivatives.get("oi_change_4h"))
        crowding_long = ratio <= 0.90 or funding <= -0.0001 or oi_change <= -0.03
        crowding_short = ratio >= 1.10 or funding >= 0.0001 or oi_change >= 0.03

    if long_extreme:
        confidence = 0.48 + 0.08 * _clip(abs(min(bb_z, 0.0)) / 3.0)
        if crowding_long:
            confidence += 0.15
        return V7Candidate(
            species="mean_reversion_range",
            side="LONG",
            confidence=_clip(confidence),
            reason="range_extreme_reversion"
            + ("_crowding_confirmed" if crowding_long else ""),
            regime_key=regime.key,
            research_mode="historical_research_only",
            crowding_confirmed=crowding_long,
        )

    if short_extreme:
        confidence = 0.48 + 0.08 * _clip(max(bb_z, 0.0) / 3.0)
        if crowding_short:
            confidence += 0.15
        return V7Candidate(
            species="mean_reversion_range",
            side="SHORT",
            confidence=_clip(confidence),
            reason="range_extreme_reversion"
            + ("_crowding_confirmed" if crowding_short else ""),
            regime_key=regime.key,
            research_mode="historical_research_only",
            crowding_confirmed=crowding_short,
        )
    return None


def generate_price_candidates(
    features: Mapping[str, object],
    regime: RegimeState,
    *,
    derivatives: Mapping[str, object] | None = None,
) -> list[V7Candidate]:
    candidates: list[V7Candidate] = []
    for species in route_species(regime, features):
        if species == "trend_breakout":
            candidate = _trend_breakout_candidate(features, regime)
        elif species == "mean_reversion_range":
            candidate = _mean_reversion_candidate(features, regime, derivatives)
        else:
            candidate = None
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def generate_microstructure_candidate(
    *,
    symbol: str,
    bybit: Mapping[str, object],
    binance: Mapping[str, object],
    regime_key: str,
) -> V7Candidate | None:
    """Cross-venue order-flow edge. It is intentionally live-shadow only."""
    if symbol not in {"BTCUSDT", "ETHUSDT", "BTC/USDT", "ETH/USDT"}:
        raise ValueError("unsupported V7 symbol")

    bybit_imb = _finite(bybit.get("imbalance"))
    binance_imb = _finite(binance.get("imbalance"))
    bybit_spread = _finite(bybit.get("spread_bps"), 999.0)
    binance_spread = _finite(binance.get("spread_bps"), 999.0)

    if max(bybit_spread, binance_spread) > 8.0:
        return None
    if abs(bybit_imb) < 0.15 or abs(binance_imb) < 0.15:
        return None
    if bybit_imb * binance_imb <= 0:
        return None

    avg_imbalance = (bybit_imb + binance_imb) / 2.0
    side = "LONG" if avg_imbalance > 0 else "SHORT"
    confidence = _clip(0.45 + abs(avg_imbalance) * 0.8)
    return V7Candidate(
        species="microstructure_orderflow",
        side=side,
        confidence=confidence,
        reason="cross_venue_depth_imbalance_alignment",
        regime_key=regime_key,
        research_mode="live_shadow_only",
    )


def multi_horizon_outcome(
    candles: list[dict],
    *,
    index: int,
    side: str,
    round_trip_cost: float = 0.0015,
) -> V7Outcome:
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    if round_trip_cost < 0:
        raise ValueError("round_trip_cost cannot be negative")
    if index < 0 or index + 12 >= len(candles):
        raise ValueError("V7 outcome requires twelve future 15m candles")

    direction = 1.0 if side == "LONG" else -1.0
    entry = float(candles[index]["close"])
    if entry <= 0:
        raise ValueError("entry price must be positive")

    def net_at(offset: int) -> float:
        exit_price = float(candles[index + offset]["close"])
        gross = direction * ((exit_price / entry) - 1.0)
        return gross - round_trip_cost

    directional_highs: list[float] = []
    directional_lows: list[float] = []
    for future in candles[index + 1 : index + 13]:
        high = float(future["high"])
        low = float(future["low"])
        if side == "LONG":
            directional_highs.append((high / entry) - 1.0)
            directional_lows.append((low / entry) - 1.0)
        else:
            directional_highs.append((entry / low) - 1.0)
            directional_lows.append((entry / high) - 1.0)

    return V7Outcome(
        net_return_30m=net_at(2),
        net_return_60m=net_at(4),
        net_return_180m=net_at(12),
        mae_180m=float(min(directional_lows)),
        mfe_180m=float(max(directional_highs)),
    )


def meta_feature_vector(
    features: Mapping[str, object],
    candidate: V7Candidate,
    regime: RegimeState,
    *,
    timestamp_ms: int,
) -> dict[str, float]:
    """Model inputs are limited to information available at decision time."""
    ts = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    minute_of_day = ts.hour * 60 + ts.minute
    angle = 2.0 * math.pi * minute_of_day / 1440.0

    species_code = {
        "trend_breakout": 0.0,
        "mean_reversion_range": 1.0,
        "microstructure_orderflow": 2.0,
    }[candidate.species]
    trend_code = {"trend_down": -1.0, "range": 0.0, "trend_up": 1.0}[regime.trend]
    vol_code = {"low_vol": -1.0, "mid_vol": 0.0, "high_vol": 1.0}[regime.volatility]
    momentum_code = {
        "momentum_down": -1.0,
        "momentum_mixed": 0.0,
        "momentum_up": 1.0,
    }[regime.momentum]

    close = max(_finite(features.get("close"), 1.0), 1e-12)
    atr = max(_finite(features.get("atr_14"), 0.0), 0.0)
    return {
        "side": 1.0 if candidate.side == "LONG" else -1.0,
        "species_code": species_code,
        "candidate_confidence": float(candidate.confidence),
        "trend_code": trend_code,
        "volatility_code": vol_code,
        "momentum_code": momentum_code,
        "adx_14": _finite(features.get("adx_14")) / 100.0,
        "rsi_14": _finite(features.get("rsi_14"), 50.0) / 100.0,
        "atr_normalized": atr / close,
        "ema_gap_atr": _finite(features.get("ema_gap_atr")),
        "roc_10": _finite(features.get("roc_10")),
        "return_1": _finite(features.get("return_1")),
        "return_4": _finite(features.get("return_4")),
        "return_16": _finite(features.get("return_16")),
        "volume_z_20": _finite(features.get("volume_z_20")),
        "bb_z_20": _finite(features.get("bb_z_20")),
        "stoch_k": _finite(features.get("stoch_k"), 50.0) / 100.0,
        "atr_normalized_move": _finite(features.get("atr_normalized_move")),
        "hour_sin": math.sin(angle),
        "hour_cos": math.cos(angle),
    }
