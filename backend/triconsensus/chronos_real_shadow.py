from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path

import numpy as np

from .chronos_hybrid import ChronosArtificialMarketCivilization
from .chronos_memory import MarketMemoryIndex
from .chronos_replay import ChronosRealReplaySource
from .config import Settings
from .db import SupabaseRepository
from .market_data import BybitPublicClient


def _evaluation_metrics(model, transitions, evaluation_rows: int) -> dict:
    rows = list(transitions[-evaluation_rows:])
    actual_return = np.array([row.next_return for row in rows], dtype=float)
    predicted_return = np.array(
        [model.predict(row.state).next_return_mean for row in rows],
        dtype=float,
    )
    lows = np.array([model.predict(row.state).next_return_low for row in rows], dtype=float)
    highs = np.array([model.predict(row.state).next_return_high for row in rows], dtype=float)
    directional = float(np.mean(np.sign(predicted_return) == np.sign(actual_return)))
    mae = float(np.mean(np.abs(actual_return - predicted_return)))
    rmse = float(np.sqrt(np.mean((actual_return - predicted_return) ** 2)))
    coverage = float(np.mean((actual_return >= lows) & (actual_return <= highs)))
    correlation = 0.0
    if len(rows) >= 3 and float(np.std(actual_return)) > 1e-12 and float(np.std(predicted_return)) > 1e-12:
        correlation = float(np.corrcoef(actual_return, predicted_return)[0, 1])
    return {
        "rows": len(rows),
        "directional_accuracy": directional,
        "mae": mae,
        "rmse": rmse,
        "interval_coverage": coverage,
        "prediction_actual_correlation": correlation,
        "actual_mean_return": float(np.mean(actual_return)),
        "predicted_mean_return": float(np.mean(predicted_return)),
    }


async def run_symbol(symbol: str, *, hours: int, universes: int) -> dict:
    settings = Settings()
    repo = SupabaseRepository(settings)
    market = BybitPublicClient(settings)
    source = ChronosRealReplaySource(repo, market)
    transitions, coverage = await source.build(symbol=symbol, hours=hours)

    civilization = ChronosArtificialMarketCivilization()
    civilization.fit(transitions)
    calibration = civilization.transition_model.calibration_report
    if calibration is None:
        raise RuntimeError("real replay model did not produce calibration report")

    memory = MarketMemoryIndex(
        minimum_neighbors=12,
        similarity_floor=0.62,
        max_neighbors=40,
        recency_half_life_days=14.0,
    )
    memory.fit_transitions(transitions)
    query_state = transitions[-1].state
    recall = memory.recall(query_state)

    forecast = civilization.forecast(
        query_state,
        drift_detected=False,
        universes=universes,
        horizon_steps=12,
        seed=2700,
    )
    evaluation = _evaluation_metrics(
        civilization.transition_model,
        transitions,
        calibration.evaluation_rows,
    )

    return {
        "symbol": symbol,
        "source_kind": "REAL_V6_CROSS_VENUE_REPLAY",
        "coverage": asdict(coverage),
        "calibration": {
            "train_rows": calibration.train_rows,
            "calibration_rows": calibration.calibration_rows,
            "evaluation_rows": calibration.evaluation_rows,
            "mean_interval_coverage": calibration.mean_interval_coverage,
            "calibrated": calibration.calibrated,
            "targets": [asdict(target) for target in calibration.targets],
        },
        "untouched_evaluation": evaluation,
        "memory": {
            "usable": recall.usable,
            "neighbor_count": recall.neighbor_count,
            "recurrence_score": recall.recurrence_score,
            "weighted_next_return": recall.weighted_next_return,
            "return_dispersion": recall.return_dispersion,
            "reason": recall.reason,
        },
        "civilization": {
            "version": forecast.version,
            "world_mode": forecast.world_mode,
            "research_candidate": forecast.research_candidate,
            "dominant_actor_hypothesis": forecast.dominant_actor,
            "learned_actor_ranking": forecast.learned_actor_ranking,
            "future": asdict(forecast.future),
            "trust": asdict(forecast.trust),
            "action_twin": {
                "median_counterfactual_delta": forecast.action_twin.median_counterfactual_delta,
                "action_better_probability": forecast.action_twin.action_better_probability,
                "self_impact_bps": forecast.action_twin.self_impact_bps,
                "same_random_worlds": forecast.action_twin.same_random_worlds,
            },
        },
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }


async def main() -> None:
    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
        "hours_requested": 120,
        "universes_per_symbol": 100_000,
        "symbols": [],
    }
    for symbol in ("BTCUSDT", "ETHUSDT"):
        output["symbols"].append(
            await run_symbol(symbol, hours=120, universes=100_000)
        )
    path = Path("artifacts/chronos-v88-real-shadow.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, sort_keys=True))
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
