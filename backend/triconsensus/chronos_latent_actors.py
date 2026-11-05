from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Sequence

import numpy as np


CHRONOS_LATENT_ACTOR_VERSION = "v8.12-latent-actor-archetypes-0"


@dataclass(frozen=True)
class ResidualEvent:
    timestamp_ms: int
    residual_order_flow: float
    return_1m: float
    basis_bps: float
    spread_bps: float
    depth_imbalance: float
    funding_rate: float
    open_interest_change: float
    next_order_flow: float


@dataclass(frozen=True)
class LatentActorArchetype:
    actor_id: str
    sample_count: int
    recurrence_fraction: float
    centroid: tuple[float, ...]
    mean_residual_order_flow: float
    mean_next_order_flow: float
    persistence: float
    directional_lead_score: float
    behavioral_label: str
    research_only: bool = True
    identity_claim: bool = False


@dataclass(frozen=True)
class LatentActorDiscovery:
    version: str
    events: int
    archetypes: tuple[LatentActorArchetype, ...]
    unexplained_fraction: float
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def _vector(event: ResidualEvent) -> np.ndarray:
    minute_of_day = (event.timestamp_ms // 60_000) % (24 * 60)
    angle = 2.0 * math.pi * minute_of_day / (24.0 * 60.0)
    return np.array(
        [
            event.residual_order_flow,
            math.tanh(30.0 * event.return_1m),
            math.tanh(event.basis_bps / 8.0),
            math.tanh(event.spread_bps / 8.0),
            event.depth_imbalance,
            math.tanh(event.funding_rate * 5_000.0),
            math.tanh(event.open_interest_change * 10.0),
            math.sin(angle),
            math.cos(angle),
        ],
        dtype=float,
    )


def _stable_actor_id(centroid: np.ndarray) -> str:
    quantized = ",".join(f"{value:+.2f}" for value in centroid)
    return "LATENT-ACTOR-" + hashlib.sha256(quantized.encode()).hexdigest()[:10].upper()


def _behavioral_label(centroid: np.ndarray, mean_residual: float, lead: float) -> str:
    components: list[str] = []
    if abs(mean_residual) >= 0.15:
        components.append("persistent-buy-pressure" if mean_residual > 0 else "persistent-sell-pressure")
    if abs(centroid[2]) >= 0.25:
        components.append("cross-venue-sensitive")
    if centroid[3] >= 0.20:
        components.append("wide-spread-state")
    if abs(centroid[4]) >= 0.20:
        components.append("depth-asymmetry")
    if abs(centroid[5]) >= 0.20:
        components.append("funding-sensitive")
    if abs(centroid[6]) >= 0.20:
        components.append("oi-sensitive")
    if abs(lead) >= 0.10:
        components.append("flow-leading")
    return "+".join(components) if components else "unclassified-residual-behavior"


class LatentActorArchetypeMiner:
    """Discover recurring unexplained-flow archetypes without claiming real identities."""

    def __init__(
        self,
        *,
        clusters: int = 4,
        minimum_events: int = 80,
        minimum_cluster_size: int = 12,
        seed: int = 2700,
        max_iterations: int = 80,
    ):
        if clusters < 2:
            raise ValueError("clusters must be >=2")
        if minimum_events < clusters * 10:
            raise ValueError("minimum_events too small for requested clusters")
        self.clusters = clusters
        self.minimum_events = minimum_events
        self.minimum_cluster_size = minimum_cluster_size
        self.seed = seed
        self.max_iterations = max_iterations

    @staticmethod
    def _standardize(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        mean = matrix.mean(axis=0)
        std = matrix.std(axis=0)
        std = np.where(std < 1e-9, 1.0, std)
        return (matrix - mean) / std, mean, std

    def _kmeans(self, matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(self.seed)
        centers = [matrix[int(rng.integers(0, len(matrix)))]]
        while len(centers) < self.clusters:
            distances = np.min(
                np.stack(
                    [np.sum((matrix - center) ** 2, axis=1) for center in centers],
                    axis=1,
                ),
                axis=1,
            )
            total = float(distances.sum())
            if total <= 1e-12:
                index = int(rng.integers(0, len(matrix)))
            else:
                probabilities = distances / total
                index = int(rng.choice(len(matrix), p=probabilities))
            centers.append(matrix[index])
        centers_array = np.stack(centers)
        labels = np.zeros(len(matrix), dtype=int)
        for _ in range(self.max_iterations):
            distance_matrix = np.stack(
                [np.sum((matrix - center) ** 2, axis=1) for center in centers_array],
                axis=1,
            )
            new_labels = np.argmin(distance_matrix, axis=1)
            if np.array_equal(new_labels, labels) and _ > 0:
                break
            labels = new_labels
            new_centers = []
            for cluster in range(self.clusters):
                mask = labels == cluster
                if not np.any(mask):
                    new_centers.append(centers_array[cluster])
                else:
                    new_centers.append(matrix[mask].mean(axis=0))
            centers_array = np.stack(new_centers)
        return labels, centers_array

    def discover(self, events: Sequence[ResidualEvent]) -> LatentActorDiscovery:
        ordered = sorted(events, key=lambda event: event.timestamp_ms)
        if len(ordered) < self.minimum_events:
            raise ValueError(
                f"latent actor discovery needs {self.minimum_events} events; got {len(ordered)}"
            )
        raw = np.stack([_vector(event) for event in ordered])
        matrix, mean, std = self._standardize(raw)
        labels, centers_z = self._kmeans(matrix)
        centers = centers_z * std + mean
        archetypes: list[LatentActorArchetype] = []
        explained = 0

        for cluster in range(self.clusters):
            idx = np.where(labels == cluster)[0]
            if len(idx) < self.minimum_cluster_size:
                continue
            explained += len(idx)
            cluster_events = [ordered[int(i)] for i in idx]
            residuals = np.asarray(
                [event.residual_order_flow for event in cluster_events],
                dtype=float,
            )
            next_flow = np.asarray(
                [event.next_order_flow for event in cluster_events],
                dtype=float,
            )
            mean_residual = float(np.mean(residuals))
            mean_next = float(np.mean(next_flow))
            residual_sign = np.sign(residuals)
            next_sign = np.sign(next_flow)
            persistence = float(np.mean(residual_sign == next_sign))
            lead = 0.0
            if np.std(residuals) > 1e-12 and np.std(next_flow) > 1e-12:
                lead = float(np.corrcoef(residuals, next_flow)[0, 1])
            centroid = centers[cluster]
            archetypes.append(
                LatentActorArchetype(
                    actor_id=_stable_actor_id(centroid),
                    sample_count=len(idx),
                    recurrence_fraction=len(idx) / len(ordered),
                    centroid=tuple(float(value) for value in centroid),
                    mean_residual_order_flow=mean_residual,
                    mean_next_order_flow=mean_next,
                    persistence=persistence,
                    directional_lead_score=lead,
                    behavioral_label=_behavioral_label(centroid, mean_residual, lead),
                )
            )

        archetypes.sort(
            key=lambda actor: (
                actor.sample_count * max(0.0, actor.persistence - 0.5),
                abs(actor.directional_lead_score),
            ),
            reverse=True,
        )
        return LatentActorDiscovery(
            version=CHRONOS_LATENT_ACTOR_VERSION,
            events=len(ordered),
            archetypes=tuple(archetypes),
            unexplained_fraction=max(0.0, 1.0 - explained / len(ordered)),
        )
