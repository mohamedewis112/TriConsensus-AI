from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .chronos import Intervention, MarketState
from .chronos_firewall import ResearchFirewallReport
from .chronos_hybrid import (
    ChronosArtificialMarketCivilization,
    CivilizationForecast,
)
from .chronos_immune import (
    AlphaImmuneSystem,
    RepairCandidate,
    VaccinationResult,
    propose_minimal_causal_repairs,
)
from .chronos_memory import MarketMemoryIndex, MarketMemoryRecall


CHRONOS_OS_VERSION = "v8.7-chronos-research-os-0"


@dataclass(frozen=True)
class ChronosOSReport:
    version: str
    alpha_id: str
    verdict: str
    reasons: tuple[str, ...]
    civilization: CivilizationForecast
    memory: MarketMemoryRecall
    vaccination: VaccinationResult
    firewall: ResearchFirewallReport
    repair_candidates: tuple[RepairCandidate, ...]
    paper_trading_authority: bool = False
    live_trading_authority: bool = False
    research_only: bool = True


class ChronosResearchOperatingSystem:
    """Fuse independent evidence layers into one fail-closed research verdict."""

    def __init__(
        self,
        civilization: ChronosArtificialMarketCivilization,
        memory: MarketMemoryIndex,
        *,
        immune: AlphaImmuneSystem | None = None,
        minimum_vaccination_survival: float = 0.60,
    ):
        if not 0.0 <= minimum_vaccination_survival <= 1.0:
            raise ValueError("minimum_vaccination_survival must be inside [0,1]")
        self.civilization = civilization
        self.memory = memory
        self.immune = immune or AlphaImmuneSystem()
        self.minimum_vaccination_survival = minimum_vaccination_survival

    def assess(
        self,
        *,
        alpha_id: str,
        state: MarketState,
        alpha_score_fn: Callable[[MarketState], float],
        firewall: ResearchFirewallReport,
        drift_detected: bool,
        interventions: Sequence[Intervention] | None = None,
        universes: int = 10_000,
        horizon_steps: int = 12,
        seed: int = 2700,
    ) -> ChronosOSReport:
        civilization = self.civilization.forecast(
            state,
            drift_detected=drift_detected,
            universes=universes,
            horizon_steps=horizon_steps,
            seed=seed,
        )
        memory = self.memory.recall(state)
        vaccination = self.immune.vaccinate(
            alpha_id=alpha_id,
            state=state,
            score_fn=alpha_score_fn,
            interventions=interventions,
        )
        repairs = propose_minimal_causal_repairs(vaccination)

        reasons: list[str] = []
        if not firewall.passed:
            reasons.append("research_firewall_failed")
        if not civilization.trust.research_ready:
            reasons.append("world_model_not_trusted")
        if vaccination.survival_rate < self.minimum_vaccination_survival:
            reasons.append("alpha_failed_causal_vaccination")
        if not memory.usable:
            reasons.append("insufficient_recurring_market_memory")
        if civilization.research_candidate == "NO_RESEARCH_EDGE":
            reasons.append("multiverse_has_no_directional_research_edge")

        direction = (
            1.0
            if civilization.research_candidate == "WATCH_LONG"
            else -1.0
            if civilization.research_candidate == "WATCH_SHORT"
            else 0.0
        )
        if direction != 0.0 and memory.usable:
            if direction * memory.weighted_next_return <= 0:
                reasons.append("market_memory_disagrees_with_multiverse")

        hard_reject = (
            not firewall.passed
            or not civilization.trust.research_ready
            or vaccination.survival_rate < self.minimum_vaccination_survival
        )
        if hard_reject:
            verdict = "REJECT_RESEARCH"
        elif reasons:
            verdict = "HOLD_FOR_EVIDENCE"
        else:
            verdict = civilization.research_candidate

        return ChronosOSReport(
            version=CHRONOS_OS_VERSION,
            alpha_id=alpha_id,
            verdict=verdict,
            reasons=tuple(reasons),
            civilization=civilization,
            memory=memory,
            vaccination=vaccination,
            firewall=firewall,
            repair_candidates=repairs,
        )
