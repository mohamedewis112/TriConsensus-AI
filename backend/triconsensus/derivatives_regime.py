from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


DERIVATIVES_STATE_VERSION = "v4.2-derivatives-state-frozen-1"


@dataclass(frozen=True)
class DerivativesThresholds:
    oi_change_low: float
    oi_change_high: float
    funding_low: float
    funding_high: float
    ratio_low: float
    ratio_high: float


@dataclass(frozen=True)
class DerivativesState:
    oi_state: str
    funding_state: str
    positioning_state: str
    price_oi_state: str
    key: str
    oi_change_4h: float
    funding_rate: float
    long_short_ratio: float
    price_return_4h: float


def build_derivatives_feature_frame(rows: list[dict]) -> pd.DataFrame:
    if len(rows) < 20:
        raise ValueError("at least 20 derivatives rows are required")

    frame = pd.DataFrame(rows).copy()
    required = {
        "timestamp_ms",
        "close",
        "open_interest",
        "funding_rate",
        "long_short_ratio",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing derivatives columns: {sorted(missing)}")

    frame = frame.sort_values("timestamp_ms").reset_index(drop=True)
    if frame["timestamp_ms"].duplicated().any():
        raise ValueError("duplicate derivatives timestamps")

    for column in ("close", "open_interest", "funding_rate", "long_short_ratio"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)

    if (frame["close"] <= 0).any():
        raise ValueError("close must be positive")
    if (frame["open_interest"] < 0).any():
        raise ValueError("open interest cannot be negative")
    if (frame["long_short_ratio"] <= 0).any():
        raise ValueError("long/short ratio must be positive")

    frame["price_return_1h"] = frame["close"].pct_change(4)
    frame["price_return_4h"] = frame["close"].pct_change(16)
    frame["oi_change_1h"] = frame["open_interest"].pct_change(4)
    frame["oi_change_4h"] = frame["open_interest"].pct_change(16)
    frame["ratio_change_1h"] = frame["long_short_ratio"].pct_change(4)
    return frame


def fit_derivatives_thresholds(
    feature_frame: pd.DataFrame,
    *,
    calibration_rows: int = 480,
) -> DerivativesThresholds:
    if calibration_rows < 100:
        raise ValueError("calibration_rows must be at least 100")

    calibration = feature_frame.iloc[:calibration_rows].copy()
    calibration = calibration.replace([np.inf, -np.inf], np.nan)

    oi = calibration["oi_change_4h"].dropna().to_numpy(dtype=float)
    funding = calibration["funding_rate"].dropna().to_numpy(dtype=float)
    ratio = calibration["long_short_ratio"].dropna().to_numpy(dtype=float)

    if len(oi) < 60 or len(funding) < 60 or len(ratio) < 60:
        raise ValueError("not enough calibration observations")

    oi_low, oi_high = np.quantile(oi, [0.33, 0.67])
    funding_low, funding_high = np.quantile(funding, [0.20, 0.80])
    ratio_low, ratio_high = np.quantile(ratio, [0.20, 0.80])

    values = [
        oi_low,
        oi_high,
        funding_low,
        funding_high,
        ratio_low,
        ratio_high,
    ]
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("non-finite derivatives thresholds")

    if oi_high <= oi_low:
        epsilon = max(abs(float(oi_low)) * 0.05, 1e-9)
        oi_high = oi_low + epsilon
    if funding_high <= funding_low:
        epsilon = max(abs(float(funding_low)) * 0.05, 1e-9)
        funding_high = funding_low + epsilon
    if ratio_high <= ratio_low:
        epsilon = max(abs(float(ratio_low)) * 0.05, 1e-9)
        ratio_high = ratio_low + epsilon

    return DerivativesThresholds(
        oi_change_low=float(oi_low),
        oi_change_high=float(oi_high),
        funding_low=float(funding_low),
        funding_high=float(funding_high),
        ratio_low=float(ratio_low),
        ratio_high=float(ratio_high),
    )


def classify_derivatives_state(
    row: pd.Series | dict,
    thresholds: DerivativesThresholds,
) -> DerivativesState:
    values = {
        "oi_change_4h": float(row["oi_change_4h"]),
        "funding_rate": float(row["funding_rate"]),
        "long_short_ratio": float(row["long_short_ratio"]),
        "price_return_4h": float(row["price_return_4h"]),
    }
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("derivatives state requires complete finite features")

    oi_change = values["oi_change_4h"]
    if oi_change >= thresholds.oi_change_high:
        oi_state = "oi_expansion"
    elif oi_change <= thresholds.oi_change_low:
        oi_state = "oi_contraction"
    else:
        oi_state = "oi_stable"

    funding = values["funding_rate"]
    if funding >= thresholds.funding_high:
        funding_state = "funding_positive_extreme"
    elif funding <= thresholds.funding_low:
        funding_state = "funding_negative_extreme"
    else:
        funding_state = "funding_neutral"

    ratio = values["long_short_ratio"]
    if ratio >= thresholds.ratio_high:
        positioning_state = "long_crowded"
    elif ratio <= thresholds.ratio_low:
        positioning_state = "short_crowded"
    else:
        positioning_state = "positioning_balanced"

    price_return = values["price_return_4h"]
    if price_return > 0 and oi_change > 0:
        price_oi_state = "price_up_oi_up"
    elif price_return > 0 and oi_change <= 0:
        price_oi_state = "price_up_oi_down"
    elif price_return < 0 and oi_change > 0:
        price_oi_state = "price_down_oi_up"
    elif price_return < 0 and oi_change <= 0:
        price_oi_state = "price_down_oi_down"
    else:
        price_oi_state = "price_flat"

    key = f"{oi_state}|{funding_state}|{positioning_state}"
    return DerivativesState(
        oi_state=oi_state,
        funding_state=funding_state,
        positioning_state=positioning_state,
        price_oi_state=price_oi_state,
        key=key,
        oi_change_4h=oi_change,
        funding_rate=funding,
        long_short_ratio=ratio,
        price_return_4h=price_return,
    )
