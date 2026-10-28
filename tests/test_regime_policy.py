from triconsensus.regime_policy import (
    RegimePersistenceGuard,
    regime_policy_allows,
)


def test_regime_transition_blocks_until_persistent():
    guard = RegimePersistenceGuard(minimum_consecutive=3)
    a = guard.update("trend_up|mid_vol|momentum_up")
    b = guard.update("trend_up|mid_vol|momentum_up")
    c = guard.update("trend_up|mid_vol|momentum_up")
    assert a.tradable is False
    assert b.tradable is False
    assert c.tradable is True


def test_regime_flip_resets_persistence():
    guard = RegimePersistenceGuard(minimum_consecutive=2)
    guard.update("a")
    assert guard.update("a").tradable is True
    flipped = guard.update("b")
    assert flipped.tradable is False
    assert flipped.reason == "regime_transition"


def test_policy_allows_only_qualified_regime_direction_pair():
    guard = RegimePersistenceGuard(minimum_consecutive=1)
    stable = guard.update("trend_up|mid_vol|momentum_up")
    qualified = {("trend_up|mid_vol|momentum_up", "LONG")}
    assert regime_policy_allows(
        stable=stable,
        side="LONG",
        qualified_pairs=qualified,
    ) == (True, "qualified_regime")
    assert regime_policy_allows(
        stable=stable,
        side="SHORT",
        qualified_pairs=qualified,
    ) == (False, "regime_not_qualified")
