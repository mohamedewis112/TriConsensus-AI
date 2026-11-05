from __future__ import annotations

import numpy as np

from triconsensus.chronos_regime_gated_ab import (
    TARGET_REGIME,
    TARGET_SIDE,
    TARGET_SPECIES,
    _latest_gate_from_candles,
)


def _candles(n: int = 260) -> list[dict]:
    rng = np.random.default_rng(826)
    price = 3200.0
    rows = []
    for i in range(n):
        shock = float(rng.normal(-0.00015, 0.002))
        open_price = price
        price = max(100.0, price * (1.0 + shock))
        high = max(open_price, price) * (1.0 + abs(float(rng.normal(0, 0.0008))))
        low = min(open_price, price) * (1.0 - abs(float(rng.normal(0, 0.0008))))
        rows.append(
            {
                "start_ms": 1_800_000_000_000 + i * 900_000,
                "open": open_price,
                "high": high,
                "low": low,
                "close": price,
                "volume": float(1000 + rng.uniform(0, 1000)),
                "turnover": 0.0,
            }
        )
    return rows


def test_regime_gate_is_exact_and_never_invents_match():
    report = _latest_gate_from_candles(_candles())
    assert report["target"] == {
        "species": TARGET_SPECIES,
        "side": TARGET_SIDE,
        "regime_key": TARGET_REGIME,
    }
    assert isinstance(report["matched"], bool)
    if report["matched"]:
        candidate = report["matched_candidate"]
        assert candidate["species"] == TARGET_SPECIES
        assert candidate["side"] == TARGET_SIDE
        assert candidate["regime_key"] == TARGET_REGIME
