from __future__ import annotations

from triconsensus.chronos_ws_horizon_lab import HORIZONS


def test_horizon_lab_is_small_fixed_pre_registered_search():
    assert HORIZONS == (
        (250, 1),
        (500, 2),
        (1000, 4),
        (2000, 8),
    )
    assert len({ms for ms, _ in HORIZONS}) == 4
