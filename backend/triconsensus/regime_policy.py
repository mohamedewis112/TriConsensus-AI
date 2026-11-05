from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StableRegime:
    raw_regime: str
    stable_regime: str | None
    consecutive: int
    tradable: bool
    reason: str


class RegimePersistenceGuard:
    def __init__(self, *, minimum_consecutive: int = 3) -> None:
        if minimum_consecutive < 1:
            raise ValueError("minimum_consecutive must be positive")
        self.minimum_consecutive = minimum_consecutive
        self._last_raw: str | None = None
        self._consecutive = 0
        self._stable: str | None = None

    def update(self, regime: str) -> StableRegime:
        if not regime:
            raise ValueError("regime cannot be empty")

        if regime == self._last_raw:
            self._consecutive += 1
        else:
            self._last_raw = regime
            self._consecutive = 1

        if self._consecutive >= self.minimum_consecutive:
            self._stable = regime
            return StableRegime(
                raw_regime=regime,
                stable_regime=self._stable,
                consecutive=self._consecutive,
                tradable=True,
                reason="stable_regime",
            )

        return StableRegime(
            raw_regime=regime,
            stable_regime=self._stable,
            consecutive=self._consecutive,
            tradable=False,
            reason="regime_transition",
        )


def regime_policy_allows(
    *,
    stable: StableRegime,
    side: str,
    qualified_pairs: set[tuple[str, str]],
) -> tuple[bool, str]:
    if not stable.tradable or stable.stable_regime is None:
        return False, "regime_transition"
    if side not in {"LONG", "SHORT"}:
        return False, "invalid_side"
    if (stable.stable_regime, side) not in qualified_pairs:
        return False, "regime_not_qualified"
    return True, "qualified_regime"
