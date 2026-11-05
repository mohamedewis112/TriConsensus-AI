from __future__ import annotations

import json

import numpy as np

from .chronos import MarketState
from .chronos_hybrid import ChronosArtificialMarketCivilization
from .chronos_learning import ChronosTransition


def build_training_set(n: int = 420) -> list[ChronosTransition]:
    rng = np.random.default_rng(2700)
    rows = []
    for i in range(n):
        ofi = float(rng.uniform(-0.9, 0.9))
        ret5 = float(rng.normal(0, 0.0045))
        state = MarketState(
            symbol="ETHUSDT",
            timestamp_ms=1_800_000_000_000 + i * 60_000,
            mid_price=3200.0,
            return_1m=0.35 * ret5,
            return_5m=ret5,
            realized_volatility=0.018,
            bid_depth=1300.0 + 240.0 * ofi,
            ask_depth=1200.0 - 240.0 * ofi,
            order_flow_imbalance=ofi,
            spread_bps=1.4 + 0.8 * abs(ofi),
            funding_rate=0.00008 * np.sign(ret5),
            open_interest_change=0.015,
            liquidation_imbalance=0.18 * np.sign(ret5),
            cross_venue_basis_bps=2.5 * ret5 / 0.0045,
            cross_venue_latency_ms=85.0,
            stablecoin_flow_z=0.45 * ofi,
            dealer_gamma_z=-0.25,
            mempool_pressure_z=0.20,
            crowding_score=0.20 * np.sign(ret5),
        )
        rows.append(
            ChronosTransition(
                state=state,
                next_return=0.009 * ofi + 0.50 * ret5 + float(rng.normal(0, 0.0004)),
                next_order_flow=float(np.clip(0.72 * ofi + rng.normal(0, 0.055), -1, 1)),
                next_liquidity_change=float(
                    np.clip(-0.14 * abs(ofi) + rng.normal(0, 0.03), -1, 1)
                ),
            )
        )
    return rows


def main() -> None:
    data = build_training_set()
    system = ChronosArtificialMarketCivilization()
    system.fit(data)
    report = system.forecast(
        data[-1].state,
        drift_detected=False,
        universes=100_000,
        horizon_steps=12,
        seed=2700,
    )
    payload = {
        "version": report.version,
        "world_mode": report.world_mode,
        "universes": report.future.universes,
        "future_diversity": report.future.future_diversity,
        "effective_scenarios": report.future.effective_scenarios,
        "positive_probability": report.future.positive_probability,
        "negative_probability": report.future.negative_probability,
        "median_return": report.future.median_return,
        "p05_return": report.future.p05_return,
        "p95_return": report.future.p95_return,
        "dominant_actor": report.dominant_actor,
        "top_actor_hypotheses": report.learned_actor_ranking[:3],
        "trust": {
            "sample_count": report.trust.sample_count,
            "calibrated": report.trust.calibrated,
            "mean_interval_coverage": report.trust.mean_interval_coverage,
            "drift_detected": report.trust.drift_detected,
            "research_ready": report.trust.research_ready,
        },
        "research_candidate": report.research_candidate,
        "paper_trading_authority": report.paper_trading_authority,
        "live_trading_authority": report.live_trading_authority,
    }
    if report.paper_trading_authority or report.live_trading_authority:
        raise RuntimeError("CHRONOS V8.2 authority invariant violated")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
