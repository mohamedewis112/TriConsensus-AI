from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import hashlib
import math
from typing import Mapping, Sequence

import numpy as np


CHRONOS_VERSION = "v8.0-chronos-research-0"
CHRONOS_RESEARCH_ONLY = True
CHRONOS_PAPER_TRADING_AUTHORITY = False
CHRONOS_LIVE_TRADING_AUTHORITY = False


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class MarketState:
    symbol: str
    timestamp_ms: int
    mid_price: float
    return_1m: float = 0.0
    return_5m: float = 0.0
    realized_volatility: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    order_flow_imbalance: float = 0.0
    spread_bps: float = 0.0
    funding_rate: float = 0.0
    open_interest_change: float = 0.0
    liquidation_imbalance: float = 0.0
    cross_venue_basis_bps: float = 0.0
    cross_venue_latency_ms: float = 0.0
    stablecoin_flow_z: float = 0.0
    dealer_gamma_z: float = 0.0
    mempool_pressure_z: float = 0.0
    crowding_score: float = 0.0

    def __post_init__(self) -> None:
        if self.symbol not in {"BTCUSDT", "ETHUSDT", "BTC/USDT", "ETH/USDT"}:
            raise ValueError("unsupported CHRONOS symbol")
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        if not math.isfinite(self.mid_price) or self.mid_price <= 0:
            raise ValueError("mid_price must be positive and finite")
        if self.bid_depth < 0 or self.ask_depth < 0:
            raise ValueError("orderbook depth cannot be negative")
        if self.spread_bps < 0:
            raise ValueError("spread_bps cannot be negative")
        if self.realized_volatility < 0:
            raise ValueError("realized_volatility cannot be negative")
        for name in ("order_flow_imbalance", "liquidation_imbalance", "crowding_score"):
            if not -1.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be inside [-1,1]")

    @property
    def total_depth(self) -> float:
        return self.bid_depth + self.ask_depth

    @property
    def depth_imbalance(self) -> float:
        total = self.total_depth
        return (self.bid_depth - self.ask_depth) / total if total > 0 else 0.0

    @property
    def liquidity_thinness(self) -> float:
        depth = max(self.total_depth, 1e-9)
        spread_component = min(1.0, self.spread_bps / 20.0)
        depth_component = 1.0 / (1.0 + math.log1p(depth))
        return _clip(0.65 * spread_component + 0.35 * depth_component, 0.0, 1.0)


@dataclass(frozen=True)
class IntentContribution:
    actor: str
    actor_kind: str
    pressure: float
    confidence: float
    latency_ms: int
    rationale: str


@dataclass(frozen=True)
class IntentField:
    net_pressure: float
    uncertainty: float
    confidence: float
    dominant_actor: str
    expected_orderflow_imbalance: float
    expected_aggressive_flow_share: float
    contributions: tuple[IntentContribution, ...]
    research_only: bool = True


@dataclass(frozen=True)
class DigitalMind:
    name: str
    kind: str
    weight: float
    latency_ms: int

    def infer(self, state: MarketState) -> IntentContribution:
        ofi = state.order_flow_imbalance
        mom = math.tanh(12.0 * state.return_5m)
        short_mom = math.tanh(20.0 * state.return_1m)
        basis = math.tanh(state.cross_venue_basis_bps / 8.0)
        funding = math.tanh(state.funding_rate * 5_000.0)
        oi = math.tanh(state.open_interest_change * 8.0)
        gamma = math.tanh(state.dealer_gamma_z / 2.0)
        stable = math.tanh(state.stablecoin_flow_z / 2.5)
        mempool = math.tanh(state.mempool_pressure_z / 2.5)
        thin = state.liquidity_thinness
        liquidations = state.liquidation_imbalance
        crowding = state.crowding_score

        if self.kind == "momentum_fund":
            raw = 0.42 * mom + 0.23 * short_mom + 0.23 * ofi + 0.12 * oi
            rationale = "momentum+orderflow+oi"
        elif self.kind == "market_maker":
            raw = (-0.50 * ofi - 0.18 * short_mom + 0.20 * state.depth_imbalance)
            raw *= 0.75 + 0.50 * thin
            rationale = "inventory_rebalancing_against_toxic_flow"
        elif self.kind == "arbitrage":
            raw = -0.72 * basis - 0.18 * funding - 0.10 * crowding
            rationale = "cross_venue_basis_convergence"
        elif self.kind == "options_dealer":
            raw = (-gamma * short_mom) + 0.18 * ofi
            rationale = "dealer_gamma_hedging"
        elif self.kind == "liquidation_engine":
            raw = 0.76 * liquidations + 0.16 * short_mom + 0.08 * thin * np.sign(liquidations)
            rationale = "forced_position_unwind"
        elif self.kind == "whale_flow":
            raw = 0.42 * stable + 0.22 * mempool + 0.20 * ofi + 0.16 * oi
            rationale = "capital_migration+large_flow_proxy"
        elif self.kind == "dex_lp":
            raw = -0.36 * short_mom - 0.20 * ofi + 0.22 * mempool
            raw *= 0.70 + 0.60 * thin
            rationale = "lp_repricing+inventory_pressure"
        elif self.kind == "retail":
            raw = 0.43 * crowding + 0.32 * mom + 0.15 * funding + 0.10 * liquidations
            rationale = "crowding+momentum_chasing"
        else:
            raise ValueError(f"unsupported digital mind kind: {self.kind}")

        pressure = _clip(raw)
        signal_strength = abs(pressure)
        data_quality = 1.0 - min(0.75, state.cross_venue_latency_ms / 4_000.0)
        confidence = _clip(0.35 + 0.45 * signal_strength + 0.20 * data_quality, 0.0, 1.0)
        return IntentContribution(
            actor=self.name,
            actor_kind=self.kind,
            pressure=pressure,
            confidence=confidence,
            latency_ms=self.latency_ms,
            rationale=rationale,
        )


def default_digital_minds() -> tuple[DigitalMind, ...]:
    return (
        DigitalMind("Momentum Funds", "momentum_fund", 1.00, 180),
        DigitalMind("Market Makers", "market_maker", 1.15, 25),
        DigitalMind("Cross-Venue Arbitrage", "arbitrage", 1.10, 35),
        DigitalMind("Options Dealers", "options_dealer", 0.90, 120),
        DigitalMind("Liquidation Engines", "liquidation_engine", 1.20, 10),
        DigitalMind("Whale / Capital Flow", "whale_flow", 0.85, 500),
        DigitalMind("DEX LPs", "dex_lp", 0.70, 220),
        DigitalMind("Retail Crowd", "retail", 0.55, 850),
    )


def build_intent_field(
    state: MarketState,
    minds: Sequence[DigitalMind] | None = None,
) -> IntentField:
    minds = tuple(minds or default_digital_minds())
    if not minds:
        raise ValueError("at least one digital mind is required")
    contributions = tuple(mind.infer(state) for mind in minds)
    weights = np.array(
        [max(0.0, mind.weight) * c.confidence for mind, c in zip(minds, contributions)],
        dtype=float,
    )
    pressures = np.array([c.pressure for c in contributions], dtype=float)
    if float(weights.sum()) <= 0:
        raise ValueError("digital mind weights must contain positive mass")
    net = float(np.average(pressures, weights=weights))
    variance = float(np.average((pressures - net) ** 2, weights=weights))
    uncertainty = _clip(math.sqrt(max(variance, 0.0)), 0.0, 1.0)
    confidence = _clip((1.0 - uncertainty) * (0.45 + 0.55 * abs(net)), 0.0, 1.0)
    dominant_index = int(np.argmax(np.abs(pressures) * weights))
    expected_ofi = _clip(0.55 * state.order_flow_imbalance + 0.45 * net)
    flow_share = _clip(abs(net) * (0.55 + 0.45 * confidence), 0.0, 1.0)
    return IntentField(
        net_pressure=net,
        uncertainty=uncertainty,
        confidence=confidence,
        dominant_actor=contributions[dominant_index].actor,
        expected_orderflow_imbalance=expected_ofi,
        expected_aggressive_flow_share=flow_share,
        contributions=contributions,
    )


@dataclass(frozen=True)
class LatentActorEvidence:
    actor_id: str
    direction: str
    residual_pressure: float
    z_score: float
    persistence: float
    detected: bool
    explanation: str


class LatentActorDetector:
    def __init__(self, window: int = 40, minimum_history: int = 12, z_threshold: float = 2.5):
        if window < minimum_history or minimum_history < 5:
            raise ValueError("invalid latent-actor window")
        self.window = window
        self.minimum_history = minimum_history
        self.z_threshold = z_threshold
        self._residuals: deque[float] = deque(maxlen=window)

    def observe(self, *, predicted_order_flow: float, observed_order_flow: float) -> LatentActorEvidence:
        residual = _clip(observed_order_flow) - _clip(predicted_order_flow)
        history = np.array(self._residuals, dtype=float)
        if len(history) >= self.minimum_history:
            center = float(np.median(history))
            mad = float(np.median(np.abs(history - center)))
            scale = max(1.4826 * mad, 0.03)
            z_score = (residual - center) / scale
            recent = np.array(list(self._residuals)[-min(8, len(self._residuals)):], dtype=float)
            persistence = (
                float(np.mean(np.sign(recent) == np.sign(residual))) if residual != 0 else 0.0
            )
        else:
            z_score = 0.0
            persistence = 0.0
        detected = (
            len(history) >= self.minimum_history
            and abs(z_score) >= self.z_threshold
            and persistence >= 0.625
            and abs(residual) >= 0.10
        )
        direction = "BUY_PRESSURE" if residual > 0 else "SELL_PRESSURE" if residual < 0 else "NEUTRAL"
        fingerprint = hashlib.sha256(
            f"{direction}:{round(abs(residual), 1)}:{round(persistence, 1)}".encode()
        ).hexdigest()[:8]
        self._residuals.append(residual)
        return LatentActorEvidence(
            actor_id=f"LATENT-{fingerprint}",
            direction=direction,
            residual_pressure=residual,
            z_score=float(z_score),
            persistence=persistence,
            detected=detected,
            explanation=(
                "persistent unexplained flow detected"
                if detected
                else "residual flow remains inside unexplained-noise envelope"
            ),
        )


@dataclass(frozen=True)
class FutureLandscape:
    universes: int
    horizon_steps: int
    positive_probability: float
    negative_probability: float
    neutral_probability: float
    median_return: float
    p05_return: float
    p95_return: float
    expected_shortfall_05: float
    future_diversity: float
    effective_scenarios: float
    research_only: bool = True


@dataclass(frozen=True)
class ActionTwinResult:
    no_action: FutureLandscape
    with_action: FutureLandscape
    median_counterfactual_delta: float
    action_better_probability: float
    self_impact_bps: float
    same_random_worlds: bool
    trading_authority: bool = False


class ChronosWorldEngine:
    def __init__(self, *, round_trip_cost: float = 0.0015):
        if round_trip_cost < 0:
            raise ValueError("round_trip_cost cannot be negative")
        self.round_trip_cost = float(round_trip_cost)

    @staticmethod
    def _draw_shocks(universes: int, horizon_steps: int, seed: int) -> Mapping[str, np.ndarray]:
        if universes < 100:
            raise ValueError("CHRONOS requires at least 100 universes")
        if not 1 <= horizon_steps <= 120:
            raise ValueError("horizon_steps outside [1,120]")
        rng = np.random.default_rng(seed)
        common = rng.normal(0.0, 1.0, size=(universes, horizon_steps))
        return {
            "flow": 0.60 * common + 0.40 * rng.normal(size=(universes, horizon_steps)),
            "liquidity": rng.normal(size=(universes, horizon_steps)),
            "funding": rng.normal(size=(universes, horizon_steps)),
            "latent": rng.standard_t(df=5, size=(universes, horizon_steps)),
        }

    def _paths(
        self,
        state: MarketState,
        intent: IntentField,
        shocks: Mapping[str, np.ndarray],
        *,
        action_notional_fraction: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        flow_shock = shocks["flow"]
        universes, horizon_steps = flow_shock.shape
        base_vol = max(state.realized_volatility, 0.0005)
        step_vol = min(0.03, max(0.00015, base_vol / math.sqrt(max(horizon_steps, 1))))
        drift = (
            0.00028 * intent.net_pressure
            + 0.00010 * state.depth_imbalance
            - 0.00004 * math.tanh(state.spread_bps / 8.0)
        )
        liquidity_multiplier = np.clip(
            1.0
            + 0.22 * state.liquidity_thinness
            + 0.10 * shocks["liquidity"],
            0.55,
            1.80,
        )
        endogenous = (
            0.40 * flow_shock
            + 0.16 * shocks["funding"] * math.tanh(state.funding_rate * 5_000.0)
            + 0.14 * shocks["latent"]
        )
        step_returns = drift + step_vol * endogenous * liquidity_multiplier
        action = _clip(action_notional_fraction, -1.0, 1.0)
        depth_scale = max(math.log1p(state.total_depth), 1.0)
        initial_impact = (
            math.copysign(math.sqrt(abs(action)), action) if action != 0 else 0.0
        ) * (0.00035 + 0.00085 * state.liquidity_thinness) / depth_scale
        decay = np.exp(-np.arange(horizon_steps, dtype=float) / 2.5)
        step_returns = step_returns + initial_impact * decay[None, :]
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
            terminal, np.quantile(terminal, [0.1, 0.25, 0.5, 0.75, 0.9]), right=True
        )
        max_adverse = np.min(cumulative, axis=1)
        adverse_bins = np.digitize(
            max_adverse, np.quantile(max_adverse, [0.2, 0.4, 0.6, 0.8]), right=True
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
            future_diversity=_clip(diversity, 0.0, 1.0),
            effective_scenarios=float(math.exp(entropy)),
        )

    def simulate(
        self,
        state: MarketState,
        intent: IntentField,
        *,
        universes: int = 10_000,
        horizon_steps: int = 12,
        seed: int = 7,
        action_notional_fraction: float = 0.0,
    ) -> FutureLandscape:
        shocks = self._draw_shocks(universes, horizon_steps, seed)
        cumulative, terminal = self._paths(
            state, intent, shocks, action_notional_fraction=action_notional_fraction
        )
        return self._landscape(cumulative, terminal)

    def paired_action_twins(
        self,
        state: MarketState,
        intent: IntentField,
        *,
        action_notional_fraction: float,
        universes: int = 10_000,
        horizon_steps: int = 12,
        seed: int = 7,
    ) -> ActionTwinResult:
        if action_notional_fraction == 0:
            raise ValueError("paired action twin requires non-zero hypothetical action")
        shocks = self._draw_shocks(universes, horizon_steps, seed)
        base_cum, base_terminal = self._paths(
            state, intent, shocks, action_notional_fraction=0.0
        )
        action_cum, action_terminal = self._paths(
            state, intent, shocks, action_notional_fraction=action_notional_fraction
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


@dataclass(frozen=True)
class Intervention:
    name: str
    changes: Mapping[str, float]


@dataclass(frozen=True)
class CausalSurvivalPassport:
    direction: str
    base_median_return: float
    interventions_tested: int
    interventions_survived: int
    survival_rate: float
    minimum_fatal_intervention: str | None
    fragile_factors: tuple[str, ...]
    trading_authority: bool = False
    research_only: bool = True


def default_interventions() -> tuple[Intervention, ...]:
    return (
        Intervention(
            "liquidity_-35pct_proxy",
            {"bid_depth": 0.65, "ask_depth": 0.65, "spread_bps": 1.35},
        ),
        Intervention("orderflow_reversal", {"order_flow_imbalance": -1.0}),
        Intervention("funding_extreme_long", {"funding_rate": 0.0008, "crowding_score": 0.85}),
        Intervention("funding_extreme_short", {"funding_rate": -0.0008, "crowding_score": -0.85}),
        Intervention("liquidation_sell_cascade", {"liquidation_imbalance": -0.90}),
        Intervention("liquidation_buy_cascade", {"liquidation_imbalance": 0.90}),
        Intervention(
            "cross_venue_dislocation",
            {"cross_venue_basis_bps": 18.0, "cross_venue_latency_ms": 2_500.0},
        ),
    )


def _apply_intervention(state: MarketState, intervention: Intervention) -> MarketState:
    updates: dict[str, float] = {}
    for key, value in intervention.changes.items():
        if key not in state.__dataclass_fields__:
            raise ValueError(f"unknown intervention field: {key}")
        if key in {"bid_depth", "ask_depth", "spread_bps"} and intervention.name.startswith("liquidity_"):
            updates[key] = getattr(state, key) * value
        else:
            updates[key] = value
    return replace(state, **updates)


def build_causal_survival_passport(
    state: MarketState,
    *,
    engine: ChronosWorldEngine | None = None,
    minds: Sequence[DigitalMind] | None = None,
    interventions: Sequence[Intervention] | None = None,
    universes: int = 2_500,
    horizon_steps: int = 12,
    seed: int = 11,
) -> CausalSurvivalPassport:
    engine = engine or ChronosWorldEngine()
    minds = tuple(minds or default_digital_minds())
    interventions = tuple(interventions or default_interventions())
    base_intent = build_intent_field(state, minds)
    base = engine.simulate(
        state, base_intent, universes=universes, horizon_steps=horizon_steps, seed=seed
    )
    direction = "LONG" if base.median_return > 0 else "SHORT" if base.median_return < 0 else "NEUTRAL"
    if direction == "NEUTRAL":
        return CausalSurvivalPassport(
            direction=direction,
            base_median_return=base.median_return,
            interventions_tested=len(interventions),
            interventions_survived=0,
            survival_rate=0.0,
            minimum_fatal_intervention=interventions[0].name if interventions else None,
            fragile_factors=tuple(i.name for i in interventions),
        )
    survived = 0
    fatal: list[tuple[str, float]] = []
    base_sign = 1.0 if direction == "LONG" else -1.0
    for offset, intervention in enumerate(interventions):
        perturbed = _apply_intervention(state, intervention)
        field = build_intent_field(perturbed, minds)
        landscape = engine.simulate(
            perturbed,
            field,
            universes=universes,
            horizon_steps=horizon_steps,
            seed=seed + offset + 1,
        )
        signed = base_sign * landscape.median_return
        if signed > 0:
            survived += 1
        else:
            fatal.append((intervention.name, signed))
    fatal.sort(key=lambda item: item[1])
    return CausalSurvivalPassport(
        direction=direction,
        base_median_return=base.median_return,
        interventions_tested=len(interventions),
        interventions_survived=survived,
        survival_rate=(survived / len(interventions)) if interventions else 1.0,
        minimum_fatal_intervention=fatal[0][0] if fatal else None,
        fragile_factors=tuple(name for name, _ in fatal),
    )


@dataclass(frozen=True)
class PhysicsObservation:
    state: MarketState
    next_return: float


@dataclass(frozen=True)
class PhysicsLaw:
    name: str
    expression: str
    correlation: float
    fold_sign_consistency: float
    support: int
    score: float
    research_only: bool = True


class MarketPhysicsMiner:
    def __init__(self, minimum_support: int = 90, minimum_abs_correlation: float = 0.12):
        if minimum_support < 30:
            raise ValueError("minimum_support must be >=30")
        self.minimum_support = minimum_support
        self.minimum_abs_correlation = minimum_abs_correlation

    @staticmethod
    def _features(state: MarketState) -> Mapping[str, float]:
        return {
            "orderflow": state.order_flow_imbalance,
            "depth_imbalance": state.depth_imbalance,
            "momentum": math.tanh(15.0 * state.return_5m),
            "liquidation_x_thinness": state.liquidation_imbalance * state.liquidity_thinness,
            "basis_x_orderflow": math.tanh(state.cross_venue_basis_bps / 10.0)
            * state.order_flow_imbalance,
            "funding_x_crowding": math.tanh(state.funding_rate * 5_000.0)
            * state.crowding_score,
            "gamma_x_momentum": math.tanh(state.dealer_gamma_z / 2.0)
            * math.tanh(15.0 * state.return_5m),
            "capitalflow_x_orderflow": math.tanh(state.stablecoin_flow_z / 2.5)
            * state.order_flow_imbalance,
            "spread_x_volatility": math.tanh(state.spread_bps / 10.0)
            * min(state.realized_volatility, 0.20),
        }

    @staticmethod
    def _corr(x: np.ndarray, y: np.ndarray) -> float:
        if len(x) < 3 or float(np.std(x)) < 1e-12 or float(np.std(y)) < 1e-12:
            return 0.0
        return float(np.corrcoef(x, y)[0, 1])

    def discover(self, observations: Sequence[PhysicsObservation]) -> list[PhysicsLaw]:
        if len(observations) < self.minimum_support:
            return []
        names = tuple(self._features(observations[0].state))
        y = np.array([obs.next_return for obs in observations], dtype=float)
        rows = [self._features(obs.state) for obs in observations]
        folds = np.array_split(np.arange(len(observations)), 3)
        laws: list[PhysicsLaw] = []
        for name in names:
            x = np.array([row[name] for row in rows], dtype=float)
            corr = self._corr(x, y)
            if abs(corr) < self.minimum_abs_correlation:
                continue
            fold_corrs = [self._corr(x[idx], y[idx]) for idx in folds if len(idx) >= 10]
            expected_sign = np.sign(corr)
            consistency = float(np.mean([np.sign(v) == expected_sign for v in fold_corrs]))
            laws.append(
                PhysicsLaw(
                    name=name,
                    expression=f"next_return ~ {name}",
                    correlation=corr,
                    fold_sign_consistency=consistency,
                    support=len(observations),
                    score=abs(corr) * consistency * math.log1p(len(observations)),
                )
            )
        laws.sort(key=lambda law: law.score, reverse=True)
        return laws


@dataclass(frozen=True)
class ChronosResearchReport:
    version: str
    symbol: str
    intent: IntentField
    future: FutureLandscape
    action_twin: ActionTwinResult
    passport: CausalSurvivalPassport
    research_candidate: str
    future_consensus: float
    future_diversity: float
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def run_chronos_research_cycle(
    state: MarketState,
    *,
    universes: int = 5_000,
    horizon_steps: int = 12,
    seed: int = 17,
    hypothetical_action_fraction: float = 0.05,
) -> ChronosResearchReport:
    minds = default_digital_minds()
    intent = build_intent_field(state, minds)
    engine = ChronosWorldEngine()
    future = engine.simulate(
        state, intent, universes=universes, horizon_steps=horizon_steps, seed=seed
    )
    signed_action = hypothetical_action_fraction if future.median_return >= 0 else -hypothetical_action_fraction
    twins = engine.paired_action_twins(
        state,
        intent,
        action_notional_fraction=signed_action,
        universes=universes,
        horizon_steps=horizon_steps,
        seed=seed,
    )
    passport = build_causal_survival_passport(
        state,
        engine=engine,
        minds=minds,
        universes=max(500, min(2_500, universes // 2)),
        horizon_steps=horizon_steps,
        seed=seed + 100,
    )
    long_consensus = future.positive_probability
    short_consensus = future.negative_probability
    if (
        long_consensus >= 0.60
        and future.future_diversity >= 0.70
        and passport.direction == "LONG"
        and passport.survival_rate >= 0.50
    ):
        candidate = "WATCH_LONG"
        consensus = long_consensus
    elif (
        short_consensus >= 0.60
        and future.future_diversity >= 0.70
        and passport.direction == "SHORT"
        and passport.survival_rate >= 0.50
    ):
        candidate = "WATCH_SHORT"
        consensus = short_consensus
    else:
        candidate = "NO_RESEARCH_EDGE"
        consensus = max(long_consensus, short_consensus)
    return ChronosResearchReport(
        version=CHRONOS_VERSION,
        symbol=state.symbol,
        intent=intent,
        future=future,
        action_twin=twins,
        passport=passport,
        research_candidate=candidate,
        future_consensus=consensus,
        future_diversity=future.future_diversity,
    )
