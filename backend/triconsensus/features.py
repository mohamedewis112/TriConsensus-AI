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


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr = _atr(df, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def build_features(
    df: pd.DataFrame,
    *,
    bid: float | None = None,
    ask: float | None = None,
    orderbook: dict | None = None,
) -> dict:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if len(df) < 60:
        raise ValueError("at least 60 candles are required")

    x = df.copy()
    x["ret_1"] = x["close"].pct_change()
    x["ema_12"] = x["close"].ewm(span=12, adjust=False).mean()
    x["ema_20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["ema_26"] = x["close"].ewm(span=26, adjust=False).mean()
    x["ema_50"] = x["close"].ewm(span=50, adjust=False).mean()
    x["sma_20"] = x["close"].rolling(20).mean()
    x["rsi_14"] = _rsi(x["close"], 14)
    x["atr_14"] = _atr(x, 14)
    x["adx_14"] = _adx(x, 14)
    x["roc_10"] = x["close"].pct_change(10)
    x["realized_vol_20"] = x["ret_1"].rolling(20).std() * math.sqrt(20)

    x["macd"] = x["ema_12"] - x["ema_26"]
    x["macd_signal"] = x["macd"].ewm(span=9, adjust=False).mean()
    x["macd_hist"] = x["macd"] - x["macd_signal"]

    typical = (x["high"] + x["low"] + x["close"]) / 3
    cumulative_volume = x["volume"].cumsum().replace(0, pd.NA)
    x["vwap"] = (typical * x["volume"]).cumsum() / cumulative_volume

    lowest_14 = x["low"].rolling(14).min()
    highest_14 = x["high"].rolling(14).max()
    range_14 = (highest_14 - lowest_14).replace(0, pd.NA)
    x["stoch_k"] = 100 * (x["close"] - lowest_14) / range_14
    x["stoch_d"] = x["stoch_k"].rolling(3).mean()

    bb_mid = x["close"].rolling(20).mean()
    bb_std = x["close"].rolling(20).std()
    x["bb_upper"] = bb_mid + 2 * bb_std
    x["bb_lower"] = bb_mid - 2 * bb_std
    x["volume_z_20"] = (
        (x["volume"] - x["volume"].rolling(20).mean())
        / x["volume"].rolling(20).std().replace(0, pd.NA)
    )
    x["support_20"] = x["low"].rolling(20).min().shift(1)
    x["resistance_20"] = x["high"].rolling(20).max().shift(1)
    x["atr_normalized_move"] = x["close"].diff() / x["atr_14"].replace(0, pd.NA)

    last = x.iloc[-1]
    spread_bps = None
    if orderbook is not None:
        spread_bps = float(orderbook.get("spread_bps", 0))
    elif bid and ask and bid > 0 and ask >= bid:
        mid = (bid + ask) / 2
        spread_bps = ((ask - bid) / mid) * 10_000

    trend = "up" if last["ema_20"] > last["ema_50"] else "down"
    breakout_20 = float(last["close"]) > float(last["resistance_20"])

    return {
        "close": float(last["close"]),
        "ema_20": float(last["ema_20"]),
        "ema_50": float(last["ema_50"]),
        "sma_20": float(last["sma_20"]),
        "rsi_14": float(last["rsi_14"]),
        "macd": float(last["macd"]),
        "macd_signal": float(last["macd_signal"]),
        "macd_hist": float(last["macd_hist"]),
        "atr_14": float(last["atr_14"]),
        "adx_14": float(last["adx_14"]),
        "vwap": float(last["vwap"]),
        "stoch_k": float(last["stoch_k"]),
        "stoch_d": float(last["stoch_d"]),
        "roc_10": float(last["roc_10"]),
        "realized_vol_20": float(last["realized_vol_20"]),
        "bb_upper": float(last["bb_upper"]),
        "bb_lower": float(last["bb_lower"]),
        "volume": float(last["volume"]),
        "volume_z_20": float(last["volume_z_20"]),
        "support_20": float(last["support_20"]),
        "resistance_20": float(last["resistance_20"]),
        "atr_normalized_move": float(last["atr_normalized_move"]),
        "trend": trend,
        "breakout_20": breakout_20,
        "spread_bps": spread_bps,
        "orderbook_imbalance": (
            float(orderbook["imbalance"]) if orderbook is not None else None
        ),
        "bid_depth": float(orderbook["bid_depth"]) if orderbook is not None else None,
        "ask_depth": float(orderbook["ask_depth"]) if orderbook is not None else None,
    }
