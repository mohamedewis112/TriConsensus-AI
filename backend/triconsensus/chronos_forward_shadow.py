from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import time

import httpx

from .chronos_hybrid import ChronosArtificialMarketCivilization
from .chronos_learning import transitions_from_states
from .chronos_public_live import PublicLiveShadowCollector
from .chronos_reality_ledger import ForecastReceipt, RealityLedger
from .chronos_forward_evidence import audit_reality_ledger


class FreshForwardFailure(RuntimeError):
    pass


def resolve_due_receipts(
    *,
    ledger: RealityLedger,
    pending: dict[str, tuple[ForecastReceipt, float]],
    current_timestamp_ms: int,
    current_mid_price: float,
) -> int:
    resolved = 0
    for receipt_hash, (receipt, entry_price) in list(pending.items()):
        if current_timestamp_ms < receipt.forecast_timestamp_ms + receipt.horizon_ms:
            continue
        realized = (current_mid_price / entry_price) - 1.0
        ledger.resolve(
            receipt,
            resolved_timestamp_ms=current_timestamp_ms,
            realized_return=realized,
        )
        del pending[receipt_hash]
        resolved += 1
    return resolved


async def main() -> None:
    training_seconds = 200
    forward_seconds = 270
    forecast_every_seconds = 5
    horizon_seconds = 30
    universes = 20_000

    collector = PublicLiveShadowCollector()
    await collector.collect(seconds=training_seconds, interval_seconds=1.0)

    systems: dict[str, ChronosArtificialMarketCivilization] = {}
    ledgers: dict[str, RealityLedger] = {}
    pending: dict[str, dict[str, tuple[ForecastReceipt, float]]] = {}
    training_counts: dict[str, int] = {}

    for symbol in collector.symbols:
        transitions = transitions_from_states(collector.states[symbol])
        if len(transitions) < 180:
            raise FreshForwardFailure(
                f"{symbol} training has {len(transitions)} transitions; need >=180"
            )
        system = ChronosArtificialMarketCivilization()
        system.fit(transitions)
        systems[symbol] = system
        ledgers[symbol] = RealityLedger()
        pending[symbol] = {}
        training_counts[symbol] = len(transitions)

    timeout = httpx.Timeout(8.0)
    limits = httpx.Limits(max_connections=12, max_keepalive_connections=8)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        await collector._refresh_context(client)
        started = time.monotonic()
        sample = 0
        while time.monotonic() - started < forward_seconds:
            tick = time.monotonic()
            if sample % 15 == 0:
                await collector._refresh_context(client)
            results = await asyncio.gather(
                *[
                    collector._capture_symbol(client, symbol)
                    for symbol in collector.symbols
                ],
                return_exceptions=True,
            )
            for symbol, result in zip(collector.symbols, results):
                if isinstance(result, Exception):
                    collector.errors.append(
                        f"fresh_forward:{symbol}:{type(result).__name__}:{result}"
                    )
                    continue
                state = collector.states[symbol][-1]
                resolve_due_receipts(
                    ledger=ledgers[symbol],
                    pending=pending[symbol],
                    current_timestamp_ms=state.timestamp_ms,
                    current_mid_price=state.mid_price,
                )

                if sample % forecast_every_seconds != 0:
                    continue
                system = systems[symbol]
                gate_report = system.transition_model.calibration_report
                learned_transition = system.transition_model.predict(state)
                learned_future = system.learned_engine.simulate(
                    state,
                    learned_transition,
                    universes=universes,
                    horizon_steps=horizon_seconds,
                    seed=2700 + sample,
                )
                trusted = bool(gate_report and gate_report.predictive_skill and gate_report.calibrated)
                receipt = ledgers[symbol].register(
                    symbol=symbol,
                    forecast_timestamp_ms=state.timestamp_ms,
                    horizon_ms=horizon_seconds * 1_000,
                    model_version=learned_transition.model_version,
                    world_mode=(
                        "learned_shadow_trusted"
                        if trusted
                        else "learned_shadow_untrusted"
                    ),
                    candidate=(
                        "SHADOW_FORECAST_TRUSTED"
                        if trusted
                        else "SHADOW_FORECAST_UNTRUSTED"
                    ),
                    positive_probability=learned_future.positive_probability,
                    negative_probability=learned_future.negative_probability,
                    median_return=learned_future.median_return,
                    p05_return=learned_future.p05_return,
                    p95_return=learned_future.p95_return,
                    future_diversity=learned_future.future_diversity,
                    input_snapshot={
                        **asdict(state),
                        "learning_gate": {
                            "calibrated": bool(gate_report.calibrated) if gate_report else False,
                            "predictive_skill": bool(gate_report.predictive_skill) if gate_report else False,
                        },
                    },
                )
                pending[symbol][receipt.receipt_hash] = (receipt, state.mid_price)
            sample += 1
            delay = 1.0 - (time.monotonic() - tick)
            if delay > 0:
                await asyncio.sleep(delay)

    report = {
        "version": "v8.15-fresh-forward-qualifiable-window-0",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "PUBLIC_LIVE_OKX_L2_COINBASE_SPOT_REFERENCE",
        "training_seconds": training_seconds,
        "forward_seconds": forward_seconds,
        "forecast_every_seconds": forecast_every_seconds,
        "forecast_horizon_seconds": horizon_seconds,
        "universes_per_forecast": universes,
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
        "collector_errors": collector.errors[-30:],
        "symbols": [],
    }

    for symbol in collector.symbols:
        ledger = ledgers[symbol]
        if not ledger.verify_chain():
            raise FreshForwardFailure(f"{symbol} forecast receipt chain failed")
        summary = ledger.calibration_summary() if ledger.resolutions else None
        report["symbols"].append(
            {
                "symbol": symbol,
                "frozen_training_transitions": training_counts[symbol],
                "forecast_receipts": len(ledger.receipts),
                "resolved_forecasts": len(ledger.resolutions),
                "still_pending": len(pending[symbol]),
                "hash_chain_valid": ledger.verify_chain(),
                "fresh_forward_calibration": asdict(summary) if summary else None,
                "forward_evidence_audit": asdict(audit_reality_ledger(ledger)) if ledger.resolutions else None,
            }
        )

    path = Path("artifacts/chronos-v811-fresh-forward.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
