from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

import numpy as np

from .chronos import (
    CHRONOS_LIVE_TRADING_AUTHORITY,
    CHRONOS_PAPER_TRADING_AUTHORITY,
    DigitalMind,
    MarketState,
    build_intent_field,
    default_digital_minds,
)


CHRONOS_LEARNING_VERSION = "v8.13-chronos-learning-integrity-0"
FEATURE_NAMES = (
    "return_1m",
    "return_5m",
    "realized_volatility",
    "depth_imbalance",
    "liquidity_thinness",
    "order_flow_imbalance",
    "spread_bps_scaled",
    "funding_scaled",
    "open_interest_change",
    "liquidation_imbalance",
    "basis_scaled",
    "latency_scaled",
    "stablecoin_flow",
    "dealer_gamma",
    "mempool_pressure",
    "crowding_score",
)
TARGET_NAMES = ("next_return", "next_order_flow", "next_liquidity_change")


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def feature_vector(state: MarketState) -> np.ndarray:
    return np.array(
        [
            state.return_1m,
            state.return_5m,
            state.realized_volatility,
            state.depth_imbalance,
            state.liquidity_thinness,
            state.order_flow_imbalance,
            math.tanh(state.spread_bps / 10.0),
            math.tanh(state.funding_rate * 5_000.0),
            state.open_interest_change,
            state.liquidation_imbalance,
            math.tanh(state.cross_venue_basis_bps / 10.0),
            math.tanh(state.cross_venue_latency_ms / 2_000.0),
            math.tanh(state.stablecoin_flow_z / 3.0),
            math.tanh(state.dealer_gamma_z / 3.0),
            math.tanh(state.mempool_pressure_z / 3.0),
            state.crowding_score,
        ],
        dtype=float,
    )


@dataclass(frozen=True)
class ChronosTransition:
    state: MarketState
    next_return: float
    next_order_flow: float
    next_liquidity_change: float

    def target_vector(self) -> np.ndarray:
        return np.array(
            [
                self.next_return,
                _clip(self.next_order_flow, -1.0, 1.0),
                _clip(self.next_liquidity_change, -1.0, 1.0),
            ],
            dtype=float,
        )


def transitions_from_states(states: Sequence[MarketState]) -> list[ChronosTransition]:
    if len(states) < 2:
        return []
    rows = sorted(states, key=lambda state: state.timestamp_ms)
    transitions: list[ChronosTransition] = []
    for current, future in zip(rows, rows[1:]):
        if current.symbol != future.symbol:
            continue
        if future.timestamp_ms <= current.timestamp_ms:
            continue
        next_return = (future.mid_price / current.mid_price) - 1.0
        current_depth = max(current.total_depth, 1e-9)
        liquidity_change = (future.total_depth / current_depth) - 1.0
        transitions.append(
            ChronosTransition(
                state=current,
                next_return=float(next_return),
                next_order_flow=float(future.order_flow_imbalance),
                next_liquidity_change=float(_clip(liquidity_change, -1.0, 1.0)),
            )
        )
    return transitions


@dataclass(frozen=True)
class TargetCalibration:
    target: str
    rmse: float
    interval_coverage: float
    conformal_radius: float


@dataclass(frozen=True)
class LearningCalibrationReport:
    train_rows: int
    calibration_rows: int
    evaluation_rows: int
    targets: tuple[TargetCalibration, ...]
    mean_interval_coverage: float
    calibrated: bool
    next_return_direction_deadzone: float
    next_return_directional_sample_count: int
    next_return_directional_accuracy: float
    next_return_directional_wilson_lower_95: float
    next_return_correlation: float
    next_return_actual_mean: float
    next_return_predicted_mean: float
    next_return_mean_sign_agreement: bool
    predictive_skill: bool


@dataclass(frozen=True)
class TransitionForecast:
    next_return_mean: float
    next_return_std: float
    next_return_low: float
    next_return_high: float
    next_order_flow_mean: float
    next_order_flow_std: float
    next_order_flow_low: float
    next_order_flow_high: float
    next_liquidity_change_mean: float
    next_liquidity_change_std: float
    next_liquidity_change_low: float
    next_liquidity_change_high: float
    model_version: str
    sample_count: int
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


class ProbabilisticTransitionEnsemble:
    """Chronological ridge bootstrap ensemble with split conformal uncertainty.

    No target from the future is used in feature construction. Training is chronological:
    70% fit, 15% conformal calibration, 15% untouched evaluation.
    """

    def __init__(
        self,
        *,
        heads: int = 9,
        ridge: float = 1e-3,
        minimum_samples: int = 180,
        seed: int = 2700,
    ):
        if heads < 3:
            raise ValueError("heads must be >=3")
        if ridge <= 0:
            raise ValueError("ridge must be positive")
        if minimum_samples < 120:
            raise ValueError("minimum_samples must be >=120")
        self.heads = heads
        self.ridge = ridge
        self.minimum_samples = minimum_samples
        self.seed = seed
        self._feature_mean: np.ndarray | None = None
        self._feature_std: np.ndarray | None = None
        self._weights: list[np.ndarray] = []
        self._radii = np.zeros(len(TARGET_NAMES), dtype=float)
        self._sample_count = 0
        self.calibration_report: LearningCalibrationReport | None = None

    @staticmethod
    def _design(x: np.ndarray) -> np.ndarray:
        return np.column_stack([np.ones(len(x)), x])

    @staticmethod
    def _predict_weights(weights: np.ndarray, x: np.ndarray) -> np.ndarray:
        return ProbabilisticTransitionEnsemble._design(x) @ weights

    def _standardize(self, x: np.ndarray) -> np.ndarray:
        if self._feature_mean is None or self._feature_std is None:
            raise RuntimeError("model is not fitted")
        return (x - self._feature_mean) / self._feature_std

    def _head_predictions(self, x: np.ndarray) -> np.ndarray:
        standardized = self._standardize(x)
        return np.stack(
            [self._predict_weights(weights, standardized) for weights in self._weights],
            axis=0,
        )

    def fit(self, transitions: Sequence[ChronosTransition]) -> LearningCalibrationReport:
        ordered = sorted(transitions, key=lambda item: item.state.timestamp_ms)
        if len(ordered) < self.minimum_samples:
            raise ValueError(
                f"insufficient CHRONOS learning sample: {len(ordered)} < {self.minimum_samples}"
            )
        x = np.stack([feature_vector(item.state) for item in ordered])
        y = np.stack([item.target_vector() for item in ordered])
        n = len(ordered)
        train_end = max(1, int(n * 0.70))
        cal_end = max(train_end + 1, int(n * 0.85))
        if n - cal_end < 20:
            raise ValueError("untouched CHRONOS evaluation slice too small")

        x_train, y_train = x[:train_end], y[:train_end]
        x_cal, y_cal = x[train_end:cal_end], y[train_end:cal_end]
        x_eval, y_eval = x[cal_end:], y[cal_end:]

        self._feature_mean = x_train.mean(axis=0)
        raw_std = x_train.std(axis=0)
        self._feature_std = np.where(raw_std < 1e-9, 1.0, raw_std)
        z_train = self._standardize(x_train)

        rng = np.random.default_rng(self.seed)
        self._weights = []
        eye = np.eye(z_train.shape[1] + 1)
        eye[0, 0] = 0.0
        for _ in range(self.heads):
            idx = rng.integers(0, len(z_train), size=len(z_train))
            design = self._design(z_train[idx])
            lhs = design.T @ design + self.ridge * eye
            rhs = design.T @ y_train[idx]
            weights = np.linalg.solve(lhs, rhs)
            self._weights.append(weights)

        cal_heads = self._head_predictions(x_cal)
        cal_mean = cal_heads.mean(axis=0)
        cal_abs_error = np.abs(y_cal - cal_mean)
        self._radii = np.quantile(cal_abs_error, 0.90, axis=0, method="higher")

        eval_heads = self._head_predictions(x_eval)
        eval_mean = eval_heads.mean(axis=0)
        eval_error = y_eval - eval_mean
        targets: list[TargetCalibration] = []
        for j, target in enumerate(TARGET_NAMES):
            rmse = float(np.sqrt(np.mean(eval_error[:, j] ** 2)))
            low = eval_mean[:, j] - self._radii[j]
            high = eval_mean[:, j] + self._radii[j]
            coverage = float(np.mean((y_eval[:, j] >= low) & (y_eval[:, j] <= high)))
            targets.append(
                TargetCalibration(
                    target=target,
                    rmse=rmse,
                    interval_coverage=coverage,
                    conformal_radius=float(self._radii[j]),
                )
            )
        mean_coverage = float(np.mean([target.interval_coverage for target in targets]))

        actual_return_all = y_eval[:, 0]
        predicted_return_all = eval_mean[:, 0]
        train_abs_nonzero = np.abs(y_train[:, 0])
        train_abs_nonzero = train_abs_nonzero[train_abs_nonzero > 1e-12]
        if len(train_abs_nonzero):
            direction_deadzone = max(
                1e-8,
                float(np.quantile(train_abs_nonzero, 0.25)),
            )
        else:
            direction_deadzone = 1e-8

        active_mask = np.abs(actual_return_all) > direction_deadzone
        actual_return = actual_return_all[active_mask]
        predicted_return = predicted_return_all[active_mask]
        direction_n = len(actual_return)

        if direction_n:
            directional_hits = np.sign(predicted_return) == np.sign(actual_return)
            directional_accuracy = float(np.mean(directional_hits))
            hits = int(np.sum(directional_hits))
            z95 = 1.959963984540054
            phat = hits / direction_n
            denominator = 1.0 + (z95 * z95) / direction_n
            wilson_lower = (
                phat
                + (z95 * z95) / (2.0 * direction_n)
                - z95
                * math.sqrt(
                    (phat * (1.0 - phat)) / direction_n
                    + (z95 * z95) / (4.0 * direction_n * direction_n)
                )
            ) / denominator
        else:
            directional_accuracy = 0.0
            wilson_lower = 0.0

        return_correlation = 0.0
        if (
            direction_n >= 3
            and float(np.std(actual_return)) > 1e-12
            and float(np.std(predicted_return)) > 1e-12
        ):
            return_correlation = float(
                np.corrcoef(actual_return, predicted_return)[0, 1]
            )
        actual_mean = float(np.mean(actual_return)) if direction_n else 0.0
        predicted_mean = float(np.mean(predicted_return)) if direction_n else 0.0
        mean_sign_agreement = bool(
            direction_n > 0
            and np.sign(actual_mean) == np.sign(predicted_mean)
        )
        predictive_skill = bool(
            direction_n >= 20
            and wilson_lower > 0.50
            and return_correlation > 0.0
            and mean_sign_agreement
        )

        report = LearningCalibrationReport(
            train_rows=len(x_train),
            calibration_rows=len(x_cal),
            evaluation_rows=len(x_eval),
            targets=tuple(targets),
            mean_interval_coverage=mean_coverage,
            calibrated=0.72 <= mean_coverage <= 1.0,
            next_return_direction_deadzone=float(direction_deadzone),
            next_return_directional_sample_count=int(direction_n),
            next_return_directional_accuracy=directional_accuracy,
            next_return_directional_wilson_lower_95=float(wilson_lower),
            next_return_correlation=return_correlation,
            next_return_actual_mean=actual_mean,
            next_return_predicted_mean=predicted_mean,
            next_return_mean_sign_agreement=mean_sign_agreement,
            predictive_skill=predictive_skill,
        )
        self._sample_count = n
        self.calibration_report = report
        return report

    def predict(self, state: MarketState) -> TransitionForecast:
        if not self._weights or self.calibration_report is None:
            raise RuntimeError("CHRONOS learning model is not fitted")
        x = feature_vector(state)[None, :]
        heads = self._head_predictions(x)[:, 0, :]
        mean = heads.mean(axis=0)
        std = heads.std(axis=0)
        radii = np.maximum(self._radii, 1.64 * std)
        low = mean - radii
        high = mean + radii
        return TransitionForecast(
            next_return_mean=float(mean[0]),
            next_return_std=float(std[0]),
            next_return_low=float(low[0]),
            next_return_high=float(high[0]),
            next_order_flow_mean=float(_clip(mean[1], -1.0, 1.0)),
            next_order_flow_std=float(std[1]),
            next_order_flow_low=float(_clip(low[1], -1.0, 1.0)),
            next_order_flow_high=float(_clip(high[1], -1.0, 1.0)),
            next_liquidity_change_mean=float(_clip(mean[2], -1.0, 1.0)),
            next_liquidity_change_std=float(std[2]),
            next_liquidity_change_low=float(_clip(low[2], -1.0, 1.0)),
            next_liquidity_change_high=float(_clip(high[2], -1.0, 1.0)),
            model_version=CHRONOS_LEARNING_VERSION,
            sample_count=self._sample_count,
        )


@dataclass(frozen=True)
class DriftStatus:
    score: float
    threshold: float
    consecutive_breaches: int
    drift_detected: bool
    research_only: bool = True


class SequentialResidualDriftDetector:
    """Fail-closed drift detector calibrated on historical forecast residual norms."""

    def __init__(self, *, z_threshold: float = 4.0, consecutive_required: int = 3):
        if z_threshold <= 0:
            raise ValueError("z_threshold must be positive")
        if consecutive_required < 2:
            raise ValueError("consecutive_required must be >=2")
        self.z_threshold = z_threshold
        self.consecutive_required = consecutive_required
        self._center = 0.0
        self._scale = 1.0
        self._breaches = 0
        self._ready = False

    def fit_baseline(self, residual_vectors: Sequence[Sequence[float]]) -> None:
        if len(residual_vectors) < 30:
            raise ValueError("drift baseline needs at least 30 residual vectors")
        scores = np.linalg.norm(np.asarray(residual_vectors, dtype=float), axis=1)
        center = float(np.median(scores))
        mad = float(np.median(np.abs(scores - center)))
        self._center = center
        self._scale = max(1.4826 * mad, float(np.std(scores)) * 0.25, 1e-8)
        self._breaches = 0
        self._ready = True

    def update(self, residual_vector: Sequence[float]) -> DriftStatus:
        if not self._ready:
            raise RuntimeError("drift detector baseline is not fitted")
        raw = float(np.linalg.norm(np.asarray(residual_vector, dtype=float)))
        z = max(0.0, (raw - self._center) / self._scale)
        if z >= self.z_threshold:
            self._breaches += 1
        else:
            self._breaches = 0
        detected = self._breaches >= self.consecutive_required
        return DriftStatus(
            score=z,
            threshold=self.z_threshold,
            consecutive_breaches=self._breaches,
            drift_detected=detected,
        )


@dataclass(frozen=True)
class MindSkill:
    actor: str
    base_weight: float
    learned_skill: float
    effective_weight: float
    observations: int


class AdaptiveDigitalMindWeights:
    """Online skill-weighting of behavioural priors from realised next order flow.

    It does not claim to identify the real-world owner of an order. Instead, it learns
    which behavioural hypothesis best explains subsequent flow in the current market.
    """

    def __init__(self, minds: Sequence[DigitalMind] | None = None, *, decay: float = 0.97):
        if not 0.80 <= decay < 1.0:
            raise ValueError("decay must be in [0.80,1)")
        self.minds = tuple(minds or default_digital_minds())
        self.decay = decay
        self._skill = {mind.name: 0.50 for mind in self.minds}
        self._count = {mind.name: 0 for mind in self.minds}

    def observe(self, state: MarketState, *, realised_next_order_flow: float) -> None:
        realised = _clip(realised_next_order_flow, -1.0, 1.0)
        field = build_intent_field(state, self.minds)
        by_name = {item.actor: item for item in field.contributions}
        for mind in self.minds:
            prediction = by_name[mind.name].pressure
            absolute_error = abs(prediction - realised) / 2.0
            instant_skill = _clip(1.0 - absolute_error, 0.0, 1.0)
            prior = self._skill[mind.name]
            self._skill[mind.name] = self.decay * prior + (1.0 - self.decay) * instant_skill
            self._count[mind.name] += 1

    def skills(self) -> tuple[MindSkill, ...]:
        rows = []
        for mind in self.minds:
            skill = self._skill[mind.name]
            effective = mind.weight * (0.30 + 1.40 * skill)
            rows.append(
                MindSkill(
                    actor=mind.name,
                    base_weight=mind.weight,
                    learned_skill=skill,
                    effective_weight=effective,
                    observations=self._count[mind.name],
                )
            )
        return tuple(sorted(rows, key=lambda row: row.effective_weight, reverse=True))

    def learned_minds(self) -> tuple[DigitalMind, ...]:
        weights = {row.actor: row.effective_weight for row in self.skills()}
        return tuple(replace(mind, weight=weights[mind.name]) for mind in self.minds)


@dataclass(frozen=True)
class LearningSafetyGate:
    sample_count: int
    calibrated: bool
    mean_interval_coverage: float
    next_return_direction_deadzone: float
    next_return_directional_sample_count: int
    next_return_directional_accuracy: float
    next_return_directional_wilson_lower_95: float
    next_return_correlation: float
    predictive_skill: bool
    drift_detected: bool
    research_ready: bool
    reasons: tuple[str, ...]
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def evaluate_learning_gate(
    model: ProbabilisticTransitionEnsemble,
    *,
    drift_detected: bool,
) -> LearningSafetyGate:
    report = model.calibration_report
    reasons: list[str] = []
    sample_count = model._sample_count
    if report is None:
        reasons.append("model_not_fitted")
        coverage = 0.0
        calibrated = False
        direction_deadzone = 0.0
        directional_sample_count = 0
        directional_accuracy = 0.0
        directional_lower = 0.0
        return_correlation = 0.0
        predictive_skill = False
    else:
        coverage = report.mean_interval_coverage
        calibrated = report.calibrated
        direction_deadzone = report.next_return_direction_deadzone
        directional_sample_count = report.next_return_directional_sample_count
        directional_accuracy = report.next_return_directional_accuracy
        directional_lower = report.next_return_directional_wilson_lower_95
        return_correlation = report.next_return_correlation
        predictive_skill = report.predictive_skill
        if not calibrated:
            reasons.append("forecast_intervals_not_calibrated")
        if not predictive_skill:
            reasons.append("next_return_predictive_skill_not_proven")
    if sample_count < model.minimum_samples:
        reasons.append("insufficient_samples")
    if drift_detected:
        reasons.append("distribution_drift_detected")
    ready = not reasons
    assert CHRONOS_PAPER_TRADING_AUTHORITY is False
    assert CHRONOS_LIVE_TRADING_AUTHORITY is False
    return LearningSafetyGate(
        sample_count=sample_count,
        calibrated=calibrated,
        mean_interval_coverage=coverage,
        next_return_direction_deadzone=direction_deadzone,
        next_return_directional_sample_count=directional_sample_count,
        next_return_directional_accuracy=directional_accuracy,
        next_return_directional_wilson_lower_95=directional_lower,
        next_return_correlation=return_correlation,
        predictive_skill=predictive_skill,
        drift_detected=drift_detected,
        research_ready=ready,
        reasons=tuple(reasons),
    )
