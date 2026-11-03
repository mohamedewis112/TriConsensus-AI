from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np

from .chronos import (
    ActionTwinResult,
    ChronosWorldEngine,
    DigitalMind,
    FutureLandscape,
    MarketState,
    build_intent_field,
    default_digital_minds,
)
from .chronos_learning import (
    AdaptiveDigitalMindWeights,
    ChronosTransition,
    LearningSafetyGate,
    ProbabilisticTransitionEnsemble,
    TransitionForecast,
    evaluate_learning_gate,
)


CHRONOS_HYBRID_VERSION = "v8.2-chronos-hybrid-world-0"


@dataclass(frozen=True)
class WorldModelTrustPassport:
    mode: str
    sample_count: int
    calibrated: bool
    mean_interval_coverage: float
    drift_detected: bool
    ensemble_return_std: float
    ensemble_orderflow_std: float
    research_ready: bool
    fallback_reason: tuple[str, ...]
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


@dataclass(frozen=True)
class CivilizationForecast:
    version: str
    symbol: str
    world_mode: str
    transition: TransitionForecast | None
    future: FutureLandscape
    action_twin: ActionTwinResult
    dominant_actor: str
    learned_actor_ranking: tuple[str, ...]
    trust: WorldModelTrustPassport
    research_candidate: str
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


class LearnedMultiverseEngine:
    """Generate future paths anchored to the learned transition distribution.

    The one-step learned forecast is recursively decayed rather than assumed permanent.
    Epistemic uncertainty from the ensemble and conformal uncertainty are both injected
    into the path distribution. This is a research world model, not a price oracle.
    """

    def __init__(self, *, round_trip_cost: float = 0.0015):
        if round_trip_cost < 0:
            raise ValueError("round_trip_cost cannot be negative")
        self.round_trip_cost = float(round_trip_cost)

    @staticmethod
    def _draw_shocks(universes: int, horizon_steps: int, seed: int) -> dict[str, np.ndarray]:
        if universes < 100:
            raise ValueError("learned multiverse requires at least 100 universes")
        if not 1 <= horizon_steps <= 120:
            raise ValueError("horizon_steps outside [1,120]")
        rng = np.random.default_rng(seed)
        common = rng.normal(size=(universes, horizon_steps))
        return {
            "return": 0.65 * common + 0.35 * rng.normal(size=(universes, horizon_steps)),
            "flow": 0.45 * common + 0.55 * rng.normal(size=(universes, horizon_steps)),
            "liquidity": rng.normal(size=(universes, horizon_steps)),
            "tail": rng.standard_t(df=5, size=(universes, horizon_steps)),
        }

    def _paths(
        self,
        state: MarketState,
        forecast: TransitionForecast,
        shocks: dict[str, np.ndarray],
        *,
        action_notional_fraction: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        universes, horizon_steps = shocks["return"].shape
        decay = np.exp(-np.arange(horizon_steps, dtype=float) / 4.0)
        flow_decay = np.exp(-np.arange(horizon_steps, dtype=float) / 3.0)
        liq_decay = np.exp(-np.arange(horizon_steps, dtype=float) / 5.0)

        interval_half_width = max(
            (forecast.next_return_high - forecast.next_return_low) / 2.0,
            1e-6,
        )
        base_sigma = max(
            forecast.next_return_std,
            interval_half_width / 1.64,
            state.realized_volatility / math.sqrt(max(horizon_steps, 1)) * 0.25,
            0.00015,
        )
        predicted_flow = np.clip(
            forecast.next_order_flow_mean
            + max(forecast.next_order_flow_std, 0.03) * shocks["flow"],
            -1.0,
            1.0,
        )
        predicted_liquidity = np.clip(
            forecast.next_liquidity_change_mean
            + max(forecast.next_liquidity_change_std, 0.02) * shocks["liquidity"],
            -0.85,
            1.0,
        )

        mean_surface = forecast.next_return_mean * decay[None, :]
        flow_surface = 0.00022 * predicted_flow * flow_decay[None, :]
        thinness = np.clip(
            state.liquidity_thinness
            - 0.35 * predicted_liquidity * liq_decay[None, :],
            0.0,
            1.0,
        )
        noise = base_sigma * shocks["return"] * (0.80 + 0.75 * thinness)
        tail = (
            base_sigma
            * 0.20
            * shocks["tail"]
            * (0.50 + state.liquidity_thinness)
        )
        step_returns = mean_surface + flow_surface + noise + tail

        action = max(-1.0, min(1.0, float(action_notional_fraction)))
        if action != 0:
            impact_sign = math.copysign(1.0, action)
            impact = (
                impact_sign
                * math.sqrt(abs(action))
                * (0.00030 + 0.00100 * state.liquidity_thinness)
                / max(math.log1p(state.total_depth), 1.0)
            )
            impact_decay = np.exp(-np.arange(horizon_steps, dtype=float) / 2.25)
            step_returns = step_returns + impact * impact_decay[None, :]

        cumulative = np.cumsum(step_returns, axis=1)
        terminal = cumulative[:, -1].copy()
        if action != 0:
            terminal -= self.round_trip_cost
        return cumulative, terminal

    @staticmethod
    def _landscape(cumulative: np.ndarray, terminal: np.ndarray) -> FutureLandscape:
        universes, horizon_steps = cumulative.shape
        epsilon = 0.00025
        positive = float(np.mean(terminal > epsilon))
        negative = float(np.mean(terminal < -epsilon))
        neutral = max(0.0, 1.0 - positive - negative)
        p05 = float(np.quantile(terminal, 0.05))
        p95 = float(np.quantile(terminal, 0.95))
        tail = terminal[terminal <= p05]
        es05 = float(np.mean(tail)) if len(tail) else p05

        terminal_bins = np.digitize(
            terminal,
            np.quantile(terminal, [0.1, 0.25, 0.5, 0.75, 0.9]),
            right=True,
        )
        adverse = np.min(cumulative, axis=1)
        adverse_bins = np.digitize(
            adverse,
            np.quantile(adverse, [0.2, 0.4, 0.6, 0.8]),
            right=True,
        )
        joint = terminal_bins * 5 + adverse_bins
        _, counts = np.unique(joint, return_counts=True)
        probs = counts / counts.sum()
        entropy = float(-np.sum(probs * np.log(probs + 1e-15)))
        max_entropy = math.log(max(1, len(counts)))
        diversity = entropy / max_entropy if max_entropy > 0 else 0.0
        return FutureLandscape(
            universes=universes,
            horizon_steps=horizon_steps,
            positive_probability=positive,
            negative_probability=negative,
            neutral_probability=neutral,
            median_return=float(np.median(terminal)),
            p05_return=p05,
            p95_return=p95,
            expected_shortfall_05=es05,
            future_diversity=max(0.0, min(1.0, diversity)),
            effective_scenarios=float(math.exp(entropy)),
        )

    def simulate(
        self,
        state: MarketState,
        forecast: TransitionForecast,
        *,
        universes: int,
        horizon_steps: int,
        seed: int,
        action_notional_fraction: float = 0.0,
    ) -> FutureLandscape:
        shocks = self._draw_shocks(universes, horizon_steps, seed)
        cumulative, terminal = self._paths(
            state,
            forecast,
            shocks,
            action_notional_fraction=action_notional_fraction,
        )
        return self._landscape(cumulative, terminal)

    def paired_action_twins(
        self,
        state: MarketState,
        forecast: TransitionForecast,
        *,
        action_notional_fraction: float,
        universes: int,
        horizon_steps: int,
        seed: int,
    ) -> ActionTwinResult:
        if action_notional_fraction == 0:
            raise ValueError("paired action twin requires non-zero hypothetical action")
        shocks = self._draw_shocks(universes, horizon_steps, seed)
        base_cum, base_terminal = self._paths(
            state, forecast, shocks, action_notional_fraction=0.0
        )
        action_cum, action_terminal = self._paths(
            state,
            forecast,
            shocks,
            action_notional_fraction=action_notional_fraction,
        )
        delta = action_terminal - base_terminal
        return ActionTwinResult(
            no_action=self._landscape(base_cum, base_terminal),
            with_action=self._landscape(action_cum, action_terminal),
            median_counterfactual_delta=float(np.median(delta)),
            action_better_probability=float(np.mean(delta > 0)),
            self_impact_bps=float(np.median(delta + self.round_trip_cost) * 10_000),
            same_random_worlds=True,
        )


class ChronosArtificialMarketCivilization:
    """V8.2 orchestrator: learned actor weights + learned world + safe fallback."""

    def __init__(
        self,
        *,
        transition_model: ProbabilisticTransitionEnsemble | None = None,
        mind_learner: AdaptiveDigitalMindWeights | None = None,
        round_trip_cost: float = 0.0015,
    ):
        self.transition_model = transition_model or ProbabilisticTransitionEnsemble()
        self.mind_learner = mind_learner or AdaptiveDigitalMindWeights()
        self.learned_engine = LearnedMultiverseEngine(round_trip_cost=round_trip_cost)
        self.transparent_engine = ChronosWorldEngine(round_trip_cost=round_trip_cost)

    def fit(self, transitions: Sequence[ChronosTransition]) -> None:
        ordered = sorted(transitions, key=lambda row: row.state.timestamp_ms)
        self.transition_model.fit(ordered)
        for row in ordered:
            self.mind_learner.observe(
                row.state,
                realised_next_order_flow=row.next_order_flow,
            )

    @staticmethod
    def _trust(
        gate: LearningSafetyGate,
        forecast: TransitionForecast | None,
    ) -> WorldModelTrustPassport:
        return WorldModelTrustPassport(
            mode="learned" if gate.research_ready and forecast is not None else "transparent_fallback",
            sample_count=gate.sample_count,
            calibrated=gate.calibrated,
            mean_interval_coverage=gate.mean_interval_coverage,
            drift_detected=gate.drift_detected,
            ensemble_return_std=forecast.next_return_std if forecast else 0.0,
            ensemble_orderflow_std=forecast.next_order_flow_std if forecast else 0.0,
            research_ready=gate.research_ready,
            fallback_reason=gate.reasons,
        )

    def forecast(
        self,
        state: MarketState,
        *,
        drift_detected: bool,
        universes: int = 100_000,
        horizon_steps: int = 12,
        seed: int = 2700,
        hypothetical_action_fraction: float = 0.05,
    ) -> CivilizationForecast:
        gate = evaluate_learning_gate(
            self.transition_model,
            drift_detected=drift_detected,
        )
        learned_minds: tuple[DigitalMind, ...] = self.mind_learner.learned_minds()
        intent = build_intent_field(state, learned_minds)
        ranking = tuple(row.actor for row in self.mind_learner.skills())

        if gate.research_ready:
            transition = self.transition_model.predict(state)
            future = self.learned_engine.simulate(
                state,
                transition,
                universes=universes,
                horizon_steps=horizon_steps,
                seed=seed,
            )
            direction = 1.0 if future.median_return >= 0 else -1.0
            action_twin = self.learned_engine.paired_action_twins(
                state,
                transition,
                action_notional_fraction=direction * abs(hypothetical_action_fraction),
                universes=universes,
                horizon_steps=horizon_steps,
                seed=seed,
            )
            world_mode = "learned"
        else:
            transition = None
            future = self.transparent_engine.simulate(
                state,
                intent,
                universes=universes,
                horizon_steps=horizon_steps,
                seed=seed,
            )
            direction = 1.0 if future.median_return >= 0 else -1.0
            action_twin = self.transparent_engine.paired_action_twins(
                state,
                intent,
                action_notional_fraction=direction * abs(hypothetical_action_fraction),
                universes=universes,
                horizon_steps=horizon_steps,
                seed=seed,
            )
            world_mode = "transparent_fallback"

        trust = self._trust(gate, transition)
        long_ok = (
            future.positive_probability >= 0.62
            and future.future_diversity >= 0.70
            and future.expected_shortfall_05 > -0.04
        )
        short_ok = (
            future.negative_probability >= 0.62
            and future.future_diversity >= 0.70
            and future.expected_shortfall_05 > -0.04
        )
        if not gate.research_ready:
            candidate = "NO_RESEARCH_EDGE"
        elif long_ok:
            candidate = "WATCH_LONG"
        elif short_ok:
            candidate = "WATCH_SHORT"
        else:
            candidate = "NO_RESEARCH_EDGE"

        return CivilizationForecast(
            version=CHRONOS_HYBRID_VERSION,
            symbol=state.symbol,
            world_mode=world_mode,
            transition=transition,
            future=future,
            action_twin=action_twin,
            dominant_actor=intent.dominant_actor,
            learned_actor_ranking=ranking,
            trust=trust,
            research_candidate=candidate,
        )
