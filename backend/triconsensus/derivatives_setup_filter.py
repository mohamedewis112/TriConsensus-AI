from __future__ import annotations

from dataclasses import dataclass

from .derivatives_regime import DerivativesState
from .setups import SetupSignal


DERIVATIVES_FILTER_VERSION = "v4.2-derivatives-filter-frozen-1"


@dataclass(frozen=True)
class DerivativesFilterDecision:
    allowed: bool
    reason: str


def _crowding_veto(side: str, state: DerivativesState) -> str | None:
    if (
        side == "LONG"
        and state.funding_state == "funding_positive_extreme"
        and state.positioning_state == "long_crowded"
    ):
        return "long_crowding_veto"
    if (
        side == "SHORT"
        and state.funding_state == "funding_negative_extreme"
        and state.positioning_state == "short_crowded"
    ):
        return "short_crowding_veto"
    return None


def evaluate_derivatives_filter(
    signal: SetupSignal,
    state: DerivativesState,
) -> DerivativesFilterDecision:
    if signal.side not in {"LONG", "SHORT"}:
        return DerivativesFilterDecision(False, "invalid_side")

    crowding = _crowding_veto(signal.side, state)
    if crowding is not None:
        return DerivativesFilterDecision(False, crowding)

    if signal.name in {"trend_continuation", "breakout"}:
        if state.oi_state != "oi_expansion":
            return DerivativesFilterDecision(False, "requires_oi_expansion")
        return DerivativesFilterDecision(True, "oi_expansion_confirmed")

    if signal.name == "trend_pullback":
        if state.oi_state == "oi_contraction":
            return DerivativesFilterDecision(False, "oi_contraction_veto")
        return DerivativesFilterDecision(True, "oi_not_contracting")

    if signal.name == "range_reversion":
        if signal.side == "LONG":
            allowed = (
                state.positioning_state == "short_crowded"
                or state.funding_state == "funding_negative_extreme"
            )
            return DerivativesFilterDecision(
                allowed,
                "contrarian_short_crowding_confirmed"
                if allowed
                else "requires_short_crowding_or_negative_funding",
            )
        allowed = (
            state.positioning_state == "long_crowded"
            or state.funding_state == "funding_positive_extreme"
        )
        return DerivativesFilterDecision(
            allowed,
            "contrarian_long_crowding_confirmed"
            if allowed
            else "requires_long_crowding_or_positive_funding",
        )

    return DerivativesFilterDecision(False, "unsupported_setup")
