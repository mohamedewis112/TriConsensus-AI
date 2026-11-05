from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from .chronos import MarketState, run_chronos_research_cycle


def synthetic_state() -> MarketState:
    return MarketState(
        symbol="ETHUSDT",
        timestamp_ms=1_800_000_000_000,
        mid_price=3_200.0,
        return_1m=0.0018,
        return_5m=0.0065,
        realized_volatility=0.018,
        bid_depth=1_800.0,
        ask_depth=1_450.0,
        order_flow_imbalance=0.24,
        spread_bps=1.6,
        funding_rate=0.00010,
        open_interest_change=0.022,
        liquidation_imbalance=0.15,
        cross_venue_basis_bps=2.2,
        cross_venue_latency_ms=80.0,
        stablecoin_flow_z=0.7,
        dealer_gamma_z=-0.35,
        mempool_pressure_z=0.4,
        crowding_score=0.18,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CHRONOS research-only multiverse smoke")
    parser.add_argument("--universes", type=int, default=100_000)
    parser.add_argument("--horizon-steps", type=int, default=12)
    parser.add_argument("--seed", type=int, default=2700)
    args = parser.parse_args()
    report = run_chronos_research_cycle(
        synthetic_state(),
        universes=args.universes,
        horizon_steps=args.horizon_steps,
        seed=args.seed,
    )
    payload = {
        "version": report.version,
        "symbol": report.symbol,
        "universes": report.future.universes,
        "horizon_steps": report.future.horizon_steps,
        "research_candidate": report.research_candidate,
        "future_consensus": report.future_consensus,
        "future_diversity": report.future_diversity,
        "effective_scenarios": report.future.effective_scenarios,
        "median_return": report.future.median_return,
        "p05_return": report.future.p05_return,
        "p95_return": report.future.p95_return,
        "dominant_actor": report.intent.dominant_actor,
        "intent_pressure": report.intent.net_pressure,
        "causal_survival": asdict(report.passport),
        "action_twin": {
            "median_counterfactual_delta": report.action_twin.median_counterfactual_delta,
            "self_impact_bps": report.action_twin.self_impact_bps,
            "same_random_worlds": report.action_twin.same_random_worlds,
        },
        "paper_trading_authority": report.paper_trading_authority,
        "live_trading_authority": report.live_trading_authority,
    }
    if payload["paper_trading_authority"] or payload["live_trading_authority"]:
        raise RuntimeError("CHRONOS safety invariant violated")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
