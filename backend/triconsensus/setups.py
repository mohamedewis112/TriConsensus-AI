from __future__ import annotations

from dataclasses import dataclass
import math


SETUP_VERSION = "v4.1-frozen-quant-setups"


@dataclass(frozen=True)
class SetupSignal:
    name: str
    side: str


def generate_setups(features: dict) -> list[SetupSignal]:
    required = (
        "close",
        "ema_20",
        "ema_50",
        "rsi_14",
        "atr_14",
        "adx_14",
        "macd_hist",
        "roc_10",
        "volume_z_20",
        "support_20",
        "resistance_20",
    )
    values = {key: float(features[key]) for key in required}
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("setup features contain non-finite values")

    close = values["close"]
    ema20 = values["ema_20"]
    ema50 = values["ema_50"]
    rsi = values["rsi_14"]
    atr = values["atr_14"]
    adx = values["adx_14"]
    macd_hist = values["macd_hist"]
    roc = values["roc_10"]
    volume_z = values["volume_z_20"]
    support = values["support_20"]
    resistance = values["resistance_20"]

    if min(close, ema20, ema50, atr, support, resistance) <= 0:
        raise ValueError("setup price features must be positive")

    signals: list[SetupSignal] = []

    # 1) Trend continuation: established directional structure with momentum.
    if (
        ema20 > ema50
        and adx >= 20
        and close > ema20
        and roc > 0
        and macd_hist > 0
        and 52 <= rsi <= 75
    ):
        signals.append(SetupSignal("trend_continuation", "LONG"))
    elif (
        ema20 < ema50
        and adx >= 20
        and close < ema20
        and roc < 0
        and macd_hist < 0
        and 25 <= rsi <= 48
    ):
        signals.append(SetupSignal("trend_continuation", "SHORT"))

    # 2) Trend pullback: price revisits EMA20 while the broader EMA structure holds.
    distance_to_ema20_atr = abs(close - ema20) / atr
    if (
        ema20 > ema50
        and adx >= 18
        and distance_to_ema20_atr <= 0.35
        and 42 <= rsi <= 55
        and roc > -0.02
    ):
        signals.append(SetupSignal("trend_pullback", "LONG"))
    elif (
        ema20 < ema50
        and adx >= 18
        and distance_to_ema20_atr <= 0.35
        and 45 <= rsi <= 58
        and roc < 0.02
    ):
        signals.append(SetupSignal("trend_pullback", "SHORT"))

    # 3) Range reversion: only weak-trend environments near prior support/resistance.
    if adx < 18 and close <= support + (0.35 * atr) and rsi <= 35:
        signals.append(SetupSignal("range_reversion", "LONG"))
    elif adx < 18 and close >= resistance - (0.35 * atr) and rsi >= 65:
        signals.append(SetupSignal("range_reversion", "SHORT"))

    # 4) Breakout: close clears prior 20-candle boundary with volume confirmation.
    if (
        close > resistance
        and adx >= 18
        and volume_z >= 0.5
        and roc > 0
    ):
        signals.append(SetupSignal("breakout", "LONG"))
    elif (
        close < support
        and adx >= 18
        and volume_z >= 0.5
        and roc < 0
    ):
        signals.append(SetupSignal("breakout", "SHORT"))

    return signals
