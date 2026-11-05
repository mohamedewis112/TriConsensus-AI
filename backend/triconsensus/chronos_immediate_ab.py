from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path

import numpy as np

from .chronos_dual_clock import head_specific_trust
from .chronos_learning import ProbabilisticTransitionEnsemble, transitions_from_states
from .chronos_public_live import PublicLiveShadowCollector


ROUND_TRIP_COST = 0.0015
SIDE = "SHORT"


def _pf(values: list[float]) -> float | None:
    gp = sum(v for v in values if v > 0)
    gl = abs(sum(v for v in values if v < 0))
    return None if gl == 0 else float(gp / gl)


def _dd(values: list[float]) -> float:
    eq = peak = 1.0
    worst = 0.0
    for value in values:
        eq *= 1.0 + value
        peak = max(peak, eq)
        worst = max(worst, (peak - eq) / peak)
    return float(worst)


def evaluate(states, *, training_states: int = 190) -> dict:
    if len(states) < training_states + 35:
        raise ValueError("insufficient live states")
    model = ProbabilisticTransitionEnsemble(heads=9, minimum_samples=180, seed=824)
    model.fit(transitions_from_states(states[:training_states]))
    trust = head_specific_trust(model.calibration_report)
    direction = -1.0

    horizons = []
    for horizon in (5, 15, 30):
        baseline: list[float] = []
        filtered: list[float] = []
        taken: list[float] = []
        vetoed_cf: list[float] = []
        for i in range(training_states, len(states) - horizon):
            s = states[i]
            f = states[i + horizon]
            forecast = model.predict(s)
            allow = bool(
                direction * forecast.next_order_flow_mean >= -0.15
                and forecast.next_liquidity_change_mean >= -0.25
                and s.spread_bps <= 8.0
            )
            net = direction * ((f.mid_price / s.mid_price) - 1.0) - ROUND_TRIP_COST
            baseline.append(float(net))
            filtered.append(float(net) if allow else 0.0)
            if allow:
                taken.append(float(net))
            else:
                vetoed_cf.append(float(net))

        independent_base: list[float] = []
        independent_filtered: list[float] = []
        for i in range(training_states, len(states) - horizon, horizon):
            s = states[i]
            f = states[i + horizon]
            forecast = model.predict(s)
            allow = bool(
                direction * forecast.next_order_flow_mean >= -0.15
                and forecast.next_liquidity_change_mean >= -0.25
                and s.spread_bps <= 8.0
            )
            net = direction * ((f.mid_price / s.mid_price) - 1.0) - ROUND_TRIP_COST
            independent_base.append(float(net))
            independent_filtered.append(float(net) if allow else 0.0)

        horizons.append({
            "horizon_seconds": horizon,
            "opportunities": len(baseline),
            "taken": len(taken),
            "vetoed": len(vetoed_cf),
            "veto_rate": (len(vetoed_cf) / len(baseline)) if baseline else 0.0,
            "baseline_mean_net": float(np.mean(baseline)) if baseline else 0.0,
            "filtered_mean_net_per_opportunity": float(np.mean(filtered)) if filtered else 0.0,
            "improvement_per_opportunity": (
                float(np.mean(filtered) - np.mean(baseline)) if baseline else 0.0
            ),
            "baseline_pf": _pf(baseline),
            "filtered_taken_pf": _pf(taken),
            "baseline_max_drawdown": _dd(baseline),
            "filtered_max_drawdown": _dd(filtered),
            "vetoed_mean_counterfactual_net": (
                float(np.mean(vetoed_cf)) if vetoed_cf else 0.0
            ),
            "vetoed_losses_fraction": (
                sum(v < 0 for v in vetoed_cf) / len(vetoed_cf)
                if vetoed_cf else 0.0
            ),
            "vetoed_winners_fraction": (
                sum(v > 0 for v in vetoed_cf) / len(vetoed_cf)
                if vetoed_cf else 0.0
            ),
            "independent_count": len(independent_base),
            "independent_baseline_mean_net": (
                float(np.mean(independent_base)) if independent_base else 0.0
            ),
            "independent_filtered_mean_net": (
                float(np.mean(independent_filtered)) if independent_filtered else 0.0
            ),
        })

    return {
        "side": SIDE,
        "slow_edge_reference": "ETH trend_breakout SHORT / trend_down|mid_vol|momentum_mixed",
        "slow_regime_asserted_current": False,
        "fast_head_trust": asdict(trust),
        "horizons": horizons,
        "round_trip_cost": ROUND_TRIP_COST,
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }


async def main() -> None:
    collector = PublicLiveShadowCollector(symbols=("ETHUSDT",))
    await collector.collect(seconds=235, interval_seconds=1.0)
    report = {
        "version": "v8.24-immediate-eth-timing-ab-smoke-0",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "PUBLIC_LIVE_OKX_L2_COINBASE_SPOT_REFERENCE",
        "states": len(collector.states["ETHUSDT"]),
        "collector_errors": collector.errors[-20:],
        "ab": evaluate(collector.states["ETHUSDT"]),
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }
    improvements = [h["improvement_per_opportunity"] for h in report["ab"]["horizons"]]
    report["episode_verdict"] = (
        "TIMING_HELPED_IN_THIS_EPISODE"
        if sum(v > 0 for v in improvements) >= 2
        else "TIMING_DID_NOT_HELP_IN_THIS_EPISODE"
    )
    path = Path("artifacts/chronos-v824-immediate-ab.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
