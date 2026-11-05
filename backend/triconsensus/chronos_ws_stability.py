from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from statistics import median
from typing import Sequence

from .chronos_ws_challenger import (
    TARGET_NAMES,
    WsChallengerReport,
    WsFastExecutionModel,
    build_rows,
    collect_frames,
)


CHRONOS_WS_STABILITY_VERSION = "v8.23-ws-execution-stability-vault-0"


@dataclass(frozen=True)
class TargetStability:
    target: str
    windows: int
    qualified_windows: int
    median_relative_skill: float
    median_correlation: float
    minimum_interval_coverage: float
    stable: bool


@dataclass(frozen=True)
class SymbolStabilityReport:
    symbol: str
    windows: int
    window_reports: tuple[WsChallengerReport, ...]
    targets: tuple[TargetStability, ...]
    fast_execution_stable: bool
    reasons: tuple[str, ...]
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def audit_windows(
    reports: Sequence[WsChallengerReport],
    *,
    minimum_windows: int = 4,
    minimum_qualified_windows: int = 3,
    minimum_median_skill: float = 0.05,
    minimum_median_correlation: float = 0.10,
    minimum_coverage: float = 0.72,
) -> SymbolStabilityReport:
    if len(reports) < minimum_windows:
        raise ValueError("insufficient independent websocket windows")
    symbols = {report.symbol for report in reports}
    if len(symbols) != 1:
        raise ValueError("audit one symbol at a time")
    symbol = next(iter(symbols))

    target_audits: list[TargetStability] = []
    for target in TARGET_NAMES:
        metrics = [
            next(metric for metric in report.targets if metric.target == target)
            for report in reports
        ]
        qualified = sum(metric.qualified for metric in metrics)
        median_skill = float(median(metric.relative_skill for metric in metrics))
        median_corr = float(median(metric.correlation for metric in metrics))
        min_coverage = float(min(metric.interval_coverage for metric in metrics))
        stable = bool(
            qualified >= minimum_qualified_windows
            and median_skill >= minimum_median_skill
            and median_corr >= minimum_median_correlation
            and min_coverage >= minimum_coverage
        )
        target_audits.append(
            TargetStability(
                target=target,
                windows=len(metrics),
                qualified_windows=qualified,
                median_relative_skill=median_skill,
                median_correlation=median_corr,
                minimum_interval_coverage=min_coverage,
                stable=stable,
            )
        )

    by_target = {row.target: row for row in target_audits}
    flow_ok = by_target["future_trade_imbalance_1s"].stable
    microprice_ok = by_target["future_microprice_offset_bps"].stable
    reasons: list[str] = []
    if not flow_ok:
        reasons.append("trade_flow_skill_not_stable_across_windows")
    if not microprice_ok:
        reasons.append("microprice_skill_not_stable_across_windows")

    return SymbolStabilityReport(
        symbol=symbol,
        windows=len(reports),
        window_reports=tuple(reports),
        targets=tuple(target_audits),
        fast_execution_stable=(flow_ok and microprice_ok),
        reasons=tuple(reasons),
    )


async def run_stability_vault(
    *,
    windows: int = 4,
    seconds_per_window: float = 70.0,
) -> dict:
    if windows < 4:
        raise ValueError("stability vault requires at least four windows")
    reports_by_symbol: dict[str, list[WsChallengerReport]] = {
        "BTCUSDT": [],
        "ETHUSDT": [],
    }

    for window_index in range(windows):
        frames = await collect_frames(
            seconds=seconds_per_window,
            sample_interval_seconds=0.25,
        )
        for symbol, symbol_frames in frames.items():
            rows = build_rows(symbol_frames, horizon_steps=4)
            report = WsFastExecutionModel(
                seed=8210 + window_index,
            ).fit_and_evaluate(
                rows,
                symbol=symbol,
                frames=len(symbol_frames),
                horizon_ms=1_000,
            )
            reports_by_symbol[symbol].append(report)

    audits = [
        audit_windows(symbol_reports)
        for symbol_reports in reports_by_symbol.values()
    ]
    result = {
        "version": CHRONOS_WS_STABILITY_VERSION,
        "source": "OKX_PUBLIC_WS_BOOKS5_TRADES",
        "window_count": windows,
        "seconds_per_window": seconds_per_window,
        "target_horizon_ms": 1_000,
        "symbols": [asdict(audit) for audit in audits],
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }
    path = Path("artifacts/chronos-v823-ws-stability.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True))
    return result


async def _main() -> None:
    print(json.dumps(await run_stability_vault(), indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
