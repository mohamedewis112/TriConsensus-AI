from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import mean, median

import numpy as np


@dataclass(frozen=True)
class RegimeThresholds:
    low_vol: float
    high_vol: float


@dataclass(frozen=True)
class RegimeState:
    trend: str
    volatility: str
    momentum: str
    key: str
    trend_strength_atr: float
    realized_volatility: float
    adx: float


@dataclass(frozen=True)
class RegimeStats:
    regime: str
    side: str
    trades: int
    wins: int
    hit_rate: float
    mean_net_return: float
    median_net_return: float
    profit_factor: float | None
    profitable_folds: int
    folds: int
    worst_fold_mean: float
    bootstrap_lcb_95: float
    qualified: bool


def fit_regime_thresholds(realized_volatility: list[float]) -> RegimeThresholds:
    values = np.asarray(realized_volatility, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 30:
        raise ValueError("at least 30 volatility observations are required")
    low, high = np.quantile(values, [0.33, 0.67])
    if not math.isfinite(float(low)) or not math.isfinite(float(high)):
        raise ValueError("invalid volatility thresholds")
    if high <= low:
        epsilon = max(abs(float(low)) * 0.05, 1e-9)
        high = low + epsilon
    return RegimeThresholds(low_vol=float(low), high_vol=float(high))


def classify_regime(features: dict, thresholds: RegimeThresholds) -> RegimeState:
    close = float(features["close"])
    ema20 = float(features["ema_20"])
    ema50 = float(features["ema_50"])
    atr = float(features["atr_14"])
    adx = float(features["adx_14"])
    vol = float(features["realized_vol_20"])
    macd_hist = float(features["macd_hist"])
    roc = float(features["roc_10"])

    if close <= 0 or atr <= 0:
        raise ValueError("invalid price or ATR")
    values = [close, ema20, ema50, atr, adx, vol, macd_hist, roc]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("non-finite regime inputs")

    trend_strength = (ema20 - ema50) / atr
    if adx < 18 or abs(trend_strength) < 0.35:
        trend = "range"
    elif trend_strength > 0:
        trend = "trend_up"
    else:
        trend = "trend_down"

    if vol <= thresholds.low_vol:
        volatility = "low_vol"
    elif vol >= thresholds.high_vol:
        volatility = "high_vol"
    else:
        volatility = "mid_vol"

    if macd_hist > 0 and roc > 0:
        momentum = "momentum_up"
    elif macd_hist < 0 and roc < 0:
        momentum = "momentum_down"
    else:
        momentum = "momentum_mixed"

    key = f"{trend}|{volatility}|{momentum}"
    return RegimeState(
        trend=trend,
        volatility=volatility,
        momentum=momentum,
        key=key,
        trend_strength_atr=float(trend_strength),
        realized_volatility=vol,
        adx=adx,
    )


def chronological_fold_ids(n: int, folds: int = 4) -> list[int]:
    if n <= 0 or folds < 2:
        raise ValueError("invalid fold request")
    return [min(folds - 1, (i * folds) // n) for i in range(n)]


def _profit_factor(values: list[float]) -> float | None:
    gross_profit = sum(x for x in values if x > 0)
    gross_loss = abs(sum(x for x in values if x < 0))
    if gross_loss == 0:
        return None
    return gross_profit / gross_loss


def bootstrap_lcb(
    values: list[float],
    *,
    confidence: float = 0.95,
    samples: int = 1200,
    seed: int = 42,
) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    rng = np.random.default_rng(seed)
    data = np.asarray(values, dtype=float)
    means = np.empty(samples, dtype=float)
    for i in range(samples):
        means[i] = float(np.mean(rng.choice(data, size=len(data), replace=True)))
    alpha = 1.0 - confidence
    return float(np.quantile(means, alpha))


def summarize_regime(
    regime: str,
    side: str,
    values: list[float],
    fold_ids: list[int],
    *,
    minimum_trades: int = 40,
    minimum_mean_net_return: float = 0.0003,
    minimum_profitable_fold_fraction: float = 0.67,
) -> RegimeStats:
    if len(values) != len(fold_ids):
        raise ValueError("returns and fold ids must align")
    if not values:
        return RegimeStats(
            regime=regime,
            side=side,
            trades=0,
            wins=0,
            hit_rate=0.0,
            mean_net_return=0.0,
            median_net_return=0.0,
            profit_factor=None,
            profitable_folds=0,
            folds=0,
            worst_fold_mean=0.0,
            bootstrap_lcb_95=0.0,
            qualified=False,
        )

    unique_folds = sorted(set(fold_ids))
    fold_means = []
    for fold in unique_folds:
        fold_values = [v for v, fid in zip(values, fold_ids, strict=True) if fid == fold]
        if fold_values:
            fold_means.append(mean(fold_values))

    profitable_folds = sum(1 for value in fold_means if value > 0)
    required_profitable = max(
        2,
        math.ceil(len(fold_means) * minimum_profitable_fold_fraction),
    )
    lcb = bootstrap_lcb(values)
    avg = mean(values)
    qualified = (
        len(values) >= minimum_trades
        and avg >= minimum_mean_net_return
        and profitable_folds >= required_profitable
        and lcb > 0
    )

    return RegimeStats(
        regime=regime,
        side=side,
        trades=len(values),
        wins=sum(1 for x in values if x > 0),
        hit_rate=sum(1 for x in values if x > 0) / len(values),
        mean_net_return=float(avg),
        median_net_return=float(median(values)),
        profit_factor=_profit_factor(values),
        profitable_folds=profitable_folds,
        folds=len(fold_means),
        worst_fold_mean=float(min(fold_means)) if fold_means else 0.0,
        bootstrap_lcb_95=lcb,
        qualified=qualified,
    )
