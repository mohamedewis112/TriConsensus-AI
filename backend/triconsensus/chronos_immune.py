from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Callable, Mapping, Sequence

from .chronos import Intervention, MarketState, default_interventions


CHRONOS_IMMUNE_VERSION = "v8.5-chronos-alpha-immune-0"


@dataclass(frozen=True)
class FailureSignature:
    alpha_id: str
    intervention_name: str
    changed_factors: tuple[str, ...]
    base_score: float
    stressed_score: float
    severity: float
    sign_flipped: bool
    fingerprint: str
    research_only: bool = True


@dataclass(frozen=True)
class VaccinationResult:
    version: str
    alpha_id: str
    base_score: float
    interventions_tested: int
    survived: int
    survival_rate: float
    minimum_fatal_intervention: str | None
    failures: tuple[FailureSignature, ...]
    fragile_factors: tuple[str, ...]
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


@dataclass(frozen=True)
class RepairCandidate:
    alpha_id: str
    repair_id: str
    guard: str
    covered_failures: tuple[str, ...]
    estimated_failure_coverage: float
    minimality_score: float
    reason: str
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _severity(intervention: Intervention) -> float:
    score = 0.0
    for key, value in intervention.changes.items():
        value = float(value)
        if key in {"bid_depth", "ask_depth", "spread_bps"} and intervention.name.startswith("liquidity_"):
            score += abs(1.0 - value)
        elif key in {"order_flow_imbalance", "liquidation_imbalance", "crowding_score"}:
            score += abs(value)
        elif key == "funding_rate":
            score += min(1.0, abs(value) * 1_000.0)
        elif key == "cross_venue_basis_bps":
            score += min(1.0, abs(value) / 20.0)
        elif key == "cross_venue_latency_ms":
            score += min(1.0, abs(value) / 3_000.0)
        else:
            score += min(1.0, abs(value))
    return max(1e-6, score / max(1, len(intervention.changes)))


def apply_intervention(state: MarketState, intervention: Intervention) -> MarketState:
    data = dict(state.__dict__)
    for key, value in intervention.changes.items():
        if key not in data:
            raise ValueError(f"unknown intervention factor: {key}")
        if key in {"bid_depth", "ask_depth", "spread_bps"} and intervention.name.startswith("liquidity_"):
            data[key] = float(data[key]) * float(value)
        else:
            data[key] = float(value)
    return MarketState(**data)


def _fingerprint(alpha_id: str, intervention: Intervention, base_score: float, stressed_score: float) -> str:
    raw = (
        f"{alpha_id}|{intervention.name}|"
        f"{','.join(sorted(intervention.changes))}|"
        f"{base_score:.8f}|{stressed_score:.8f}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class AlphaImmuneSystem:
    """Stress an alpha under controlled causal interventions.

    score_fn must return a signed research score where positive/negative direction has
    semantic meaning for that alpha. CHRONOS never turns this score into an order.
    """

    def __init__(
        self,
        *,
        minimum_survival_magnitude: float = 1e-6,
    ):
        if minimum_survival_magnitude < 0:
            raise ValueError("minimum_survival_magnitude cannot be negative")
        self.minimum_survival_magnitude = minimum_survival_magnitude

    def vaccinate(
        self,
        *,
        alpha_id: str,
        state: MarketState,
        score_fn: Callable[[MarketState], float],
        interventions: Sequence[Intervention] | None = None,
    ) -> VaccinationResult:
        interventions = tuple(interventions or default_interventions())
        base_score = float(score_fn(state))
        if base_score == 0:
            return VaccinationResult(
                version=CHRONOS_IMMUNE_VERSION,
                alpha_id=alpha_id,
                base_score=0.0,
                interventions_tested=len(interventions),
                survived=0,
                survival_rate=0.0,
                minimum_fatal_intervention=interventions[0].name if interventions else None,
                failures=(),
                fragile_factors=(),
            )

        base_sign = 1.0 if base_score > 0 else -1.0
        failures: list[FailureSignature] = []
        survived = 0
        for intervention in interventions:
            stressed = apply_intervention(state, intervention)
            stressed_score = float(score_fn(stressed))
            same_direction = stressed_score * base_sign > self.minimum_survival_magnitude
            if same_direction:
                survived += 1
                continue
            severity = _severity(intervention)
            failures.append(
                FailureSignature(
                    alpha_id=alpha_id,
                    intervention_name=intervention.name,
                    changed_factors=tuple(sorted(intervention.changes)),
                    base_score=base_score,
                    stressed_score=stressed_score,
                    severity=severity,
                    sign_flipped=(stressed_score * base_sign < 0),
                    fingerprint=_fingerprint(alpha_id, intervention, base_score, stressed_score),
                )
            )
        failures.sort(key=lambda failure: (failure.severity, abs(failure.stressed_score)))
        fragile = tuple(
            factor
            for factor, _ in sorted(
                {
                    factor: sum(1 for failure in failures if factor in failure.changed_factors)
                    for failure in failures
                    for factor in failure.changed_factors
                }.items(),
                key=lambda item: (item[1], item[0]),
                reverse=True,
            )
        )
        return VaccinationResult(
            version=CHRONOS_IMMUNE_VERSION,
            alpha_id=alpha_id,
            base_score=base_score,
            interventions_tested=len(interventions),
            survived=survived,
            survival_rate=(survived / len(interventions)) if interventions else 1.0,
            minimum_fatal_intervention=failures[0].intervention_name if failures else None,
            failures=tuple(failures),
            fragile_factors=fragile,
        )


class FailureImmuneMemory:
    def __init__(self) -> None:
        self._signatures: dict[str, FailureSignature] = {}
        self._counts: dict[str, int] = {}

    def remember(self, result: VaccinationResult) -> None:
        for failure in result.failures:
            self._signatures[failure.fingerprint] = failure
            self._counts[failure.fingerprint] = self._counts.get(failure.fingerprint, 0) + 1

    def recurrent_failures(self, *, alpha_id: str, minimum_count: int = 2) -> tuple[FailureSignature, ...]:
        rows = [
            self._signatures[fingerprint]
            for fingerprint, count in self._counts.items()
            if count >= minimum_count and self._signatures[fingerprint].alpha_id == alpha_id
        ]
        rows.sort(
            key=lambda row: (
                self._counts[row.fingerprint],
                -row.severity,
            ),
            reverse=True,
        )
        return tuple(rows)

    def counts(self) -> Mapping[str, int]:
        return dict(self._counts)


def propose_minimal_causal_repairs(
    result: VaccinationResult,
) -> tuple[RepairCandidate, ...]:
    if not result.failures:
        return ()
    factor_to_failures: dict[str, list[FailureSignature]] = {}
    for failure in result.failures:
        for factor in failure.changed_factors:
            factor_to_failures.setdefault(factor, []).append(failure)

    guard_by_factor = {
        "order_flow_imbalance": "require_orderflow_alignment",
        "bid_depth": "require_liquidity_floor",
        "ask_depth": "require_liquidity_floor",
        "spread_bps": "require_spread_and_depth_guard",
        "funding_rate": "require_funding_crowding_guard",
        "crowding_score": "require_funding_crowding_guard",
        "liquidation_imbalance": "require_liquidation_direction_guard",
        "cross_venue_basis_bps": "require_cross_venue_dislocation_guard",
        "cross_venue_latency_ms": "require_cross_venue_freshness_guard",
    }
    total = len(result.failures)
    candidates: dict[str, dict] = {}
    for factor, failures in factor_to_failures.items():
        guard = guard_by_factor.get(factor)
        if guard is None:
            continue
        entry = candidates.setdefault(
            guard,
            {"failures": set(), "factors": set()},
        )
        entry["factors"].add(factor)
        entry["failures"].update(failure.intervention_name for failure in failures)

    repairs: list[RepairCandidate] = []
    for guard, entry in candidates.items():
        covered = tuple(sorted(entry["failures"]))
        coverage = len(covered) / total
        factor_count = len(entry["factors"])
        minimality = coverage / max(1, factor_count)
        fingerprint = hashlib.sha256(
            f"{result.alpha_id}|{guard}|{','.join(covered)}".encode()
        ).hexdigest()[:12]
        repairs.append(
            RepairCandidate(
                alpha_id=result.alpha_id,
                repair_id=f"REPAIR-{fingerprint}",
                guard=guard,
                covered_failures=covered,
                estimated_failure_coverage=coverage,
                minimality_score=minimality,
                reason=(
                    "smallest deterministic guard derived from observed causal failure signatures; "
                    "must pass fresh shadow validation before use"
                ),
            )
        )
    repairs.sort(
        key=lambda repair: (
            repair.estimated_failure_coverage,
            repair.minimality_score,
        ),
        reverse=True,
    )
    return tuple(repairs)
