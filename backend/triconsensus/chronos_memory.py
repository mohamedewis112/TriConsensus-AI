from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

import numpy as np

from .chronos import MarketState
from .chronos_learning import ChronosTransition, FEATURE_NAMES, feature_vector


CHRONOS_MEMORY_VERSION = "v8.4-chronos-market-memory-0"


@dataclass(frozen=True)
class AlphaMemoryOutcome:
    alpha_id: str
    net_return: float
    profit_factor_proxy: float | None = None
    survived: bool = False


@dataclass(frozen=True)
class MarketEpisode:
    state: MarketState
    next_return: float
    next_order_flow: float
    next_liquidity_change: float
    alpha_outcomes: tuple[AlphaMemoryOutcome, ...] = ()
    latent_actor_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryNeighbor:
    timestamp_ms: int
    similarity: float
    age_minutes: float
    next_return: float
    next_order_flow: float
    next_liquidity_change: float
    alpha_outcomes: tuple[AlphaMemoryOutcome, ...]
    latent_actor_ids: tuple[str, ...]


@dataclass(frozen=True)
class AlphaMemoryScore:
    alpha_id: str
    weighted_samples: float
    raw_samples: int
    weighted_mean_net_return: float
    survival_rate: float
    score: float


@dataclass(frozen=True)
class MarketMemoryRecall:
    version: str
    as_of_timestamp_ms: int
    neighbor_count: int
    recurrence_score: float
    weighted_next_return: float
    weighted_next_order_flow: float
    weighted_liquidity_change: float
    return_dispersion: float
    top_alpha: str | None
    alpha_scores: tuple[AlphaMemoryScore, ...]
    recurring_latent_actors: tuple[str, ...]
    neighbors: tuple[MemoryNeighbor, ...]
    usable: bool
    reason: str
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


class MarketMemoryIndex:
    """Leakage-safe nearest-state memory over past market episodes only."""

    def __init__(
        self,
        *,
        minimum_neighbors: int = 12,
        similarity_floor: float = 0.72,
        max_neighbors: int = 40,
        recency_half_life_days: float = 45.0,
    ):
        if minimum_neighbors < 5:
            raise ValueError("minimum_neighbors must be >=5")
        if not 0.0 < similarity_floor < 1.0:
            raise ValueError("similarity_floor must be in (0,1)")
        if max_neighbors < minimum_neighbors:
            raise ValueError("max_neighbors must be >= minimum_neighbors")
        if recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive")
        self.minimum_neighbors = minimum_neighbors
        self.similarity_floor = similarity_floor
        self.max_neighbors = max_neighbors
        self.recency_half_life_days = recency_half_life_days
        self._episodes: list[MarketEpisode] = []
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None

    def fit(self, episodes: Sequence[MarketEpisode]) -> None:
        ordered = sorted(episodes, key=lambda episode: episode.state.timestamp_ms)
        if len(ordered) < self.minimum_neighbors:
            raise ValueError("insufficient episodes for CHRONOS market memory")
        matrix = np.stack([feature_vector(episode.state) for episode in ordered])
        self._mean = matrix.mean(axis=0)
        raw_std = matrix.std(axis=0)
        self._std = np.where(raw_std < 1e-9, 1.0, raw_std)
        self._episodes = list(ordered)

    def fit_transitions(self, transitions: Sequence[ChronosTransition]) -> None:
        self.fit(
            [
                MarketEpisode(
                    state=row.state,
                    next_return=row.next_return,
                    next_order_flow=row.next_order_flow,
                    next_liquidity_change=row.next_liquidity_change,
                )
                for row in transitions
            ]
        )

    def add_episode(self, episode: MarketEpisode) -> None:
        self._episodes.append(episode)
        self._episodes.sort(key=lambda item: item.state.timestamp_ms)
        matrix = np.stack([feature_vector(item.state) for item in self._episodes])
        self._mean = matrix.mean(axis=0)
        raw_std = matrix.std(axis=0)
        self._std = np.where(raw_std < 1e-9, 1.0, raw_std)

    def _scaled(self, state: MarketState) -> np.ndarray:
        if self._mean is None or self._std is None:
            raise RuntimeError("market memory is not fitted")
        vector = (feature_vector(state) - self._mean) / self._std
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 1e-12 else vector

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        an = float(np.linalg.norm(a))
        bn = float(np.linalg.norm(b))
        if an < 1e-12 or bn < 1e-12:
            return 0.0
        return float(np.dot(a, b) / (an * bn))

    def recall(self, state: MarketState) -> MarketMemoryRecall:
        if not self._episodes:
            raise RuntimeError("market memory is empty")
        query = self._scaled(state)
        candidates: list[tuple[float, MarketEpisode]] = []
        for episode in self._episodes:
            # Strict causal boundary: only episodes that happened before the query.
            if episode.state.timestamp_ms >= state.timestamp_ms:
                continue
            vector = self._scaled(episode.state)
            similarity = self._cosine(query, vector)
            if similarity >= self.similarity_floor:
                candidates.append((similarity, episode))
        candidates.sort(key=lambda item: item[0], reverse=True)
        selected = candidates[: self.max_neighbors]

        if not selected:
            return MarketMemoryRecall(
                version=CHRONOS_MEMORY_VERSION,
                as_of_timestamp_ms=state.timestamp_ms,
                neighbor_count=0,
                recurrence_score=0.0,
                weighted_next_return=0.0,
                weighted_next_order_flow=0.0,
                weighted_liquidity_change=0.0,
                return_dispersion=0.0,
                top_alpha=None,
                alpha_scores=(),
                recurring_latent_actors=(),
                neighbors=(),
                usable=False,
                reason="no_past_state_above_similarity_floor",
            )

        neighbors: list[MemoryNeighbor] = []
        weights: list[float] = []
        alpha_rows: dict[str, list[tuple[float, AlphaMemoryOutcome]]] = {}
        latent_weight: dict[str, float] = {}
        half_life_minutes = self.recency_half_life_days * 24.0 * 60.0

        for similarity, episode in selected:
            age_minutes = max(
                0.0,
                (state.timestamp_ms - episode.state.timestamp_ms) / 60_000.0,
            )
            recency = math.exp(-math.log(2.0) * age_minutes / half_life_minutes)
            weight = max(1e-9, similarity**3 * recency)
            weights.append(weight)
            neighbors.append(
                MemoryNeighbor(
                    timestamp_ms=episode.state.timestamp_ms,
                    similarity=similarity,
                    age_minutes=age_minutes,
                    next_return=episode.next_return,
                    next_order_flow=episode.next_order_flow,
                    next_liquidity_change=episode.next_liquidity_change,
                    alpha_outcomes=episode.alpha_outcomes,
                    latent_actor_ids=episode.latent_actor_ids,
                )
            )
            for alpha in episode.alpha_outcomes:
                alpha_rows.setdefault(alpha.alpha_id, []).append((weight, alpha))
            for actor_id in episode.latent_actor_ids:
                latent_weight[actor_id] = latent_weight.get(actor_id, 0.0) + weight

        w = np.asarray(weights, dtype=float)
        w = w / w.sum()
        returns = np.asarray([item.next_return for _, item in selected], dtype=float)
        flows = np.asarray([item.next_order_flow for _, item in selected], dtype=float)
        liquidity = np.asarray(
            [item.next_liquidity_change for _, item in selected],
            dtype=float,
        )
        weighted_return = float(np.dot(w, returns))
        weighted_flow = float(np.dot(w, flows))
        weighted_liquidity = float(np.dot(w, liquidity))
        dispersion = float(np.sqrt(np.dot(w, (returns - weighted_return) ** 2)))
        recurrence = float(np.dot(w, np.asarray([item[0] for item in selected])))

        alpha_scores: list[AlphaMemoryScore] = []
        for alpha_id, rows in alpha_rows.items():
            total_weight = sum(weight for weight, _ in rows)
            if total_weight <= 0:
                continue
            mean_net = sum(weight * row.net_return for weight, row in rows) / total_weight
            survival = sum(weight for weight, row in rows if row.survived) / total_weight
            sample_penalty = min(1.0, len(rows) / 12.0)
            score = mean_net * (0.5 + 0.5 * survival) * sample_penalty
            alpha_scores.append(
                AlphaMemoryScore(
                    alpha_id=alpha_id,
                    weighted_samples=total_weight,
                    raw_samples=len(rows),
                    weighted_mean_net_return=float(mean_net),
                    survival_rate=float(survival),
                    score=float(score),
                )
            )
        alpha_scores.sort(key=lambda row: (row.score, row.raw_samples), reverse=True)
        top_alpha = alpha_scores[0].alpha_id if alpha_scores else None
        recurring_latent = tuple(
            actor_id
            for actor_id, _ in sorted(
                latent_weight.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
        )
        usable = len(selected) >= self.minimum_neighbors and recurrence >= self.similarity_floor
        reason = "memory_recurrence_qualified" if usable else "insufficient_recurrent_state_support"
        return MarketMemoryRecall(
            version=CHRONOS_MEMORY_VERSION,
            as_of_timestamp_ms=state.timestamp_ms,
            neighbor_count=len(selected),
            recurrence_score=recurrence,
            weighted_next_return=weighted_return,
            weighted_next_order_flow=weighted_flow,
            weighted_liquidity_change=weighted_liquidity,
            return_dispersion=dispersion,
            top_alpha=top_alpha,
            alpha_scores=tuple(alpha_scores),
            recurring_latent_actors=recurring_latent,
            neighbors=tuple(neighbors),
            usable=usable,
            reason=reason,
        )


def episodes_from_transitions(
    transitions: Sequence[ChronosTransition],
    *,
    alpha_outcomes_by_timestamp: Mapping[int, Sequence[AlphaMemoryOutcome]] | None = None,
    latent_actor_ids_by_timestamp: Mapping[int, Sequence[str]] | None = None,
) -> list[MarketEpisode]:
    alpha_outcomes_by_timestamp = alpha_outcomes_by_timestamp or {}
    latent_actor_ids_by_timestamp = latent_actor_ids_by_timestamp or {}
    return [
        MarketEpisode(
            state=row.state,
            next_return=row.next_return,
            next_order_flow=row.next_order_flow,
            next_liquidity_change=row.next_liquidity_change,
            alpha_outcomes=tuple(alpha_outcomes_by_timestamp.get(row.state.timestamp_ms, ())),
            latent_actor_ids=tuple(latent_actor_ids_by_timestamp.get(row.state.timestamp_ms, ())),
        )
        for row in transitions
    ]
