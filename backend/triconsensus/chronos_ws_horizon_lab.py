from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from pathlib import Path

from .chronos_ws_challenger import (
    WsFastExecutionModel,
    build_rows,
    collect_frames,
)
from .chronos_ws_stability import audit_windows


CHRONOS_WS_HORIZON_VERSION = "v8.25-ws-pre-registered-horizon-lab-0"

HORIZONS = (
    (250, 1),
    (500, 2),
    (1000, 4),
    (2000, 8),
)


async def run_horizon_lab(
    *,
    windows: int = 4,
    seconds_per_window: float = 70.0,
) -> dict:
    reports: dict[str, dict[int, list]] = {
        "BTCUSDT": {ms: [] for ms, _ in HORIZONS},
        "ETHUSDT": {ms: [] for ms, _ in HORIZONS},
    }

    for window_index in range(windows):
        frames = await collect_frames(
            seconds=seconds_per_window,
            sample_interval_seconds=0.25,
        )
        for symbol, symbol_frames in frames.items():
            for horizon_ms, horizon_steps in HORIZONS:
                rows = build_rows(symbol_frames, horizon_steps=horizon_steps)
                report = WsFastExecutionModel(
                    seed=8250 + window_index * 20 + horizon_steps,
                ).fit_and_evaluate(
                    rows,
                    symbol=symbol,
                    frames=len(symbol_frames),
                    horizon_ms=horizon_ms,
                )
                reports[symbol][horizon_ms].append(report)

    symbol_results = []
    for symbol, by_horizon in reports.items():
        horizon_results = []
        candidates = []
        for horizon_ms, _ in HORIZONS:
            audit = audit_windows(by_horizon[horizon_ms])
            flow = next(
                row for row in audit.targets
                if row.target == "future_trade_imbalance_1s"
            )
            micro = next(
                row for row in audit.targets
                if row.target == "future_microprice_offset_bps"
            )
            score = min(
                flow.median_relative_skill,
                micro.median_relative_skill,
            )
            row = {
                "horizon_ms": horizon_ms,
                "stability": asdict(audit),
                "discovery_score": float(score),
                "discovery_candidate": bool(audit.fast_execution_stable),
                "fresh_forward_required": True,
            }
            horizon_results.append(row)
            if audit.fast_execution_stable:
                candidates.append(row)

        candidates.sort(
            key=lambda row: row["discovery_score"],
            reverse=True,
        )
        selected = candidates[0]["horizon_ms"] if candidates else None
        symbol_results.append(
            {
                "symbol": symbol,
                "tested_horizons_ms": [ms for ms, _ in HORIZONS],
                "horizons": horizon_results,
                "selected_discovery_horizon_ms": selected,
                "selection_is_final_evidence": False,
                "fresh_forward_required": True,
            }
        )

    result = {
        "version": CHRONOS_WS_HORIZON_VERSION,
        "source": "OKX_PUBLIC_WS_BOOKS5_TRADES",
        "pre_registered_horizons_ms": [ms for ms, _ in HORIZONS],
        "window_count": windows,
        "seconds_per_window": seconds_per_window,
        "symbols": symbol_results,
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }
    path = Path("artifacts/chronos-v825-ws-horizon-lab.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True))
    return result


async def _main() -> None:
    print(json.dumps(await run_horizon_lab(), indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
