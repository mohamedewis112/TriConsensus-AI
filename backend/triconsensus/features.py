from __future__ import annotations

import math
import pandas as pd


REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def build_features(df: pd.DataFrame, *, bid: float | None = None, ask: float | None = None) -> dict:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if len(df) < 60:
        raise ValueError("at least 60 candles are required")

    x = df.copy()
    x["ret_1"] = x["close"].pct_change()
    x["ema_20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["ema_50"] = x["close"].ewm(span=50, adjust=False).mean()
    x["sma_20"] = x["close"].rolling(20).mean()
    x["rsi_14"] = _rsi(x["close"], 14)
    x["atr_14"] = _atr(x, 14)
    x["roc_10"] = x["close"].pct_change(10)
    x["realized_vol_20"] = x["ret_1"].rolling(20).std() * math.sqrt(20)

    bb_mid = x["close"].rolling(20).mean()
    bb_std = x["close"].rolling(20).std()
    x["bb_upper"] = bb_mid + 2 * bb_std
    x["bb_lower"] = bb_mid - 2 * bb_std
    x["volume_z_20"] = (
        (x["volume"] - x["volume"].rolling(20).mean())
        / x["volume"].rolling(20).std().replace(0, pd.NA)
    )

    last = x.iloc[-1]
    spread_bps = None
    if bid and ask and bid > 0 and ask >= bid:
        mid = (bid + ask) / 2
        spread_bps = ((ask - bid) / mid) * 10_000

    trend = "up" if last["ema_20"] > last["ema_50"] else "down"
    breakout_20 = float(last["close"]) > float(x["high"].rolling(20).max().shift(1).iloc[-1])

    return {
        "close": float(last["close"]),
        "ema_20": float(last["ema_20"]),
        "ema_50": float(last["ema_50"]),
        "sma_20": float(last["sma_20"]),
        "rsi_14": float(last["rsi_14"]),
        "atr_14": float(last["atr_14"]),
        "roc_10": float(last["roc_10"]),
        "realized_vol_20": float(last["realized_vol_20"]),
        "bb_upper": float(last["bb_upper"]),
        "bb_lower": float(last["bb_lower"]),
        "volume_z_20": float(last["volume_z_20"]),
        "trend": trend,
        "breakout_20": breakout_20,
        "spread_bps": spread_bps,
    }
