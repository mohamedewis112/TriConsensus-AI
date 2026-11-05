from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Iterable, Sequence

import numpy as np


CHRONOS_LEDGER_VERSION = "v8.10-chronos-reality-ledger-0"


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True)
class ForecastReceipt:
    ledger_version: str
    symbol: str
    forecast_timestamp_ms: int
    horizon_ms: int
    model_version: str
    world_mode: str
    candidate: str
    positive_probability: float
    negative_probability: float
    median_return: float
    p05_return: float
    p95_return: float
    future_diversity: float
    input_snapshot_hash: str
    previous_receipt_hash: str | None
    receipt_hash: str
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


@dataclass(frozen=True)
class OutcomeResolution:
    receipt_hash: str
    symbol: str
    forecast_timestamp_ms: int
    resolved_timestamp_ms: int
    realized_return: float
    realized_direction: str
    interval_hit: bool
    directional_hit: bool
    brier_score: float
    absolute_error: float
    squared_error: float
    resolution_hash: str
    research_only: bool = True


@dataclass(frozen=True)
class CalibrationSummary:
    sample_count: int
    directional_accuracy: float
    interval_coverage: float
    mean_brier_score: float
    mae: float
    rmse: float
    mean_forecast_probability: float
    empirical_positive_frequency: float
    expected_calibration_error: float
    research_only: bool = True


class RealityLedger:
    """Tamper-evident pre-registration ledger for CHRONOS forecasts.

    A forecast receipt is hash-chained before its future horizon resolves. Outcomes can
    only be attached after the horizon end. This is an in-memory primitive; persistence
    adapters can store the immutable receipt/resolution rows externally.
    """

    def __init__(self) -> None:
        self._receipts: list[ForecastReceipt] = []
        self._resolutions: dict[str, OutcomeResolution] = {}

    @property
    def receipts(self) -> tuple[ForecastReceipt, ...]:
        return tuple(self._receipts)

    @property
    def resolutions(self) -> tuple[OutcomeResolution, ...]:
        return tuple(self._resolutions.values())

    @staticmethod
    def snapshot_hash(snapshot: dict) -> str:
        return _hash(snapshot)

    def register(
        self,
        *,
        symbol: str,
        forecast_timestamp_ms: int,
        horizon_ms: int,
        model_version: str,
        world_mode: str,
        candidate: str,
        positive_probability: float,
        negative_probability: float,
        median_return: float,
        p05_return: float,
        p95_return: float,
        future_diversity: float,
        input_snapshot: dict,
    ) -> ForecastReceipt:
        if forecast_timestamp_ms < 0 or horizon_ms <= 0:
            raise ValueError("invalid forecast timing")
        if not 0.0 <= positive_probability <= 1.0:
            raise ValueError("positive_probability outside [0,1]")
        if not 0.0 <= negative_probability <= 1.0:
            raise ValueError("negative_probability outside [0,1]")
        if positive_probability + negative_probability > 1.000000001:
            raise ValueError("direction probabilities exceed one")
        if p05_return > median_return or median_return > p95_return:
            raise ValueError("invalid forecast return quantiles")
        previous = self._receipts[-1].receipt_hash if self._receipts else None
        snapshot_hash = self.snapshot_hash(input_snapshot)
        body = {
            "ledger_version": CHRONOS_LEDGER_VERSION,
            "symbol": symbol,
            "forecast_timestamp_ms": int(forecast_timestamp_ms),
            "horizon_ms": int(horizon_ms),
            "model_version": model_version,
            "world_mode": world_mode,
            "candidate": candidate,
            "positive_probability": float(positive_probability),
            "negative_probability": float(negative_probability),
            "median_return": float(median_return),
            "p05_return": float(p05_return),
            "p95_return": float(p95_return),
            "future_diversity": float(future_diversity),
            "input_snapshot_hash": snapshot_hash,
            "previous_receipt_hash": previous,
        }
        receipt_hash = _hash(body)
        receipt = ForecastReceipt(
            **body,
            receipt_hash=receipt_hash,
        )
        self._receipts.append(receipt)
        return receipt

    def verify_chain(self) -> bool:
        previous = None
        for receipt in self._receipts:
            body = asdict(receipt)
            claimed = body.pop("receipt_hash")
            body.pop("research_only")
            body.pop("paper_trading_authority")
            body.pop("live_trading_authority")
            if receipt.previous_receipt_hash != previous:
                return False
            if _hash(body) != claimed:
                return False
            previous = claimed
        return True

    def resolve(
        self,
        receipt: ForecastReceipt,
        *,
        resolved_timestamp_ms: int,
        realized_return: float,
    ) -> OutcomeResolution:
        if receipt.receipt_hash in self._resolutions:
            raise ValueError("forecast receipt already resolved")
        horizon_end = receipt.forecast_timestamp_ms + receipt.horizon_ms
        if resolved_timestamp_ms < horizon_end:
            raise ValueError("cannot resolve forecast before horizon end")
        actual_positive = realized_return > 0
        forecast_positive = receipt.positive_probability >= receipt.negative_probability
        directional_hit = forecast_positive == actual_positive
        event_probability = receipt.positive_probability
        brier = (event_probability - (1.0 if actual_positive else 0.0)) ** 2
        interval_hit = receipt.p05_return <= realized_return <= receipt.p95_return
        absolute_error = abs(realized_return - receipt.median_return)
        squared_error = (realized_return - receipt.median_return) ** 2
        body = {
            "receipt_hash": receipt.receipt_hash,
            "symbol": receipt.symbol,
            "forecast_timestamp_ms": receipt.forecast_timestamp_ms,
            "resolved_timestamp_ms": int(resolved_timestamp_ms),
            "realized_return": float(realized_return),
            "realized_direction": "UP" if actual_positive else "DOWN_OR_FLAT",
            "interval_hit": bool(interval_hit),
            "directional_hit": bool(directional_hit),
            "brier_score": float(brier),
            "absolute_error": float(absolute_error),
            "squared_error": float(squared_error),
        }
        resolution = OutcomeResolution(
            **body,
            resolution_hash=_hash(body),
        )
        self._resolutions[receipt.receipt_hash] = resolution
        return resolution

    def calibration_summary(self, *, bins: int = 10) -> CalibrationSummary:
        if bins < 2:
            raise ValueError("bins must be >=2")
        pairs = [
            (receipt, self._resolutions[receipt.receipt_hash])
            for receipt in self._receipts
            if receipt.receipt_hash in self._resolutions
        ]
        if not pairs:
            raise ValueError("no resolved forecasts")
        probs = np.asarray([receipt.positive_probability for receipt, _ in pairs], dtype=float)
        actuals = np.asarray(
            [1.0 if resolution.realized_return > 0 else 0.0 for _, resolution in pairs],
            dtype=float,
        )
        resolutions = [resolution for _, resolution in pairs]
        ece = 0.0
        edges = np.linspace(0.0, 1.0, bins + 1)
        for left, right in zip(edges[:-1], edges[1:]):
            if right == 1.0:
                mask = (probs >= left) & (probs <= right)
            else:
                mask = (probs >= left) & (probs < right)
            if not np.any(mask):
                continue
            bin_weight = float(np.mean(mask))
            confidence = float(np.mean(probs[mask]))
            frequency = float(np.mean(actuals[mask]))
            ece += bin_weight * abs(confidence - frequency)
        return CalibrationSummary(
            sample_count=len(pairs),
            directional_accuracy=float(np.mean([r.directional_hit for r in resolutions])),
            interval_coverage=float(np.mean([r.interval_hit for r in resolutions])),
            mean_brier_score=float(np.mean([r.brier_score for r in resolutions])),
            mae=float(np.mean([r.absolute_error for r in resolutions])),
            rmse=float(math.sqrt(np.mean([r.squared_error for r in resolutions]))),
            mean_forecast_probability=float(np.mean(probs)),
            empirical_positive_frequency=float(np.mean(actuals)),
            expected_calibration_error=float(ece),
        )
