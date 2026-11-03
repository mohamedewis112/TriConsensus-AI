from __future__ import annotations

import json

import numpy as np

from .chronos import MarketState
from .chronos_learning import (
    ChronosTransition,
    ProbabilisticTransitionEnsemble,
    evaluate_learning_gate,
)


def _build_data(n: int = 420) -> list[ChronosTransition]:
    rng = np.random.default_rng(2700)
    rows = []
    for i in range(n):
        ofi = float(rng.uniform(-0.85, 0.85))
        ret5 = float(rng.normal(0.0, 0.0045))
        state = MarketState(
            symbol="ETHUSDT",
            timestamp_ms=1_800_000_000_000 + i * 60_000,
            mid_price=3200.0,
            return_1m=0.35 * ret5,
            return_5m=ret5,
            realized_volatility=0.018,
            bid_depth=1200.0 + 220.0 * ofi,
            ask_depth=1100.0 - 220.0 * ofi,
            order_flow_imbalance=ofi,
            spread_bps=1.4 + 0.8 * abs(ofi),
            funding_rate=0.00008 * np.sign(ret5),
            open_interest_change=0.015,
            liquidation_imbalance=0.18 * np.sign(ret5),
            cross_venue_basis_bps=2.5 * ret5 / 0.0045,
            cross_venue_latency_ms=90.0,
            stablecoin_flow_z=0.45 * ofi,
            dealer_gamma_z=-0.25,
            mempool_pressure_z=0.2,
            crowding_score=0.2 * np.sign(ret5),
        )
        rows.append(
            ChronosTransition(
                state=state,
                next_return=0.009 * ofi + 0.50 * ret5 + float(rng.normal(0, 0.0004)),
                next_order_flow=float(np.clip(0.70 * ofi + rng.normal(0, 0.055), -1, 1)),
                next_liquidity_change=float(
                    np.clip(-0.14 * abs(ofi) + rng.normal(0, 0.03), -1, 1)
                ),
            )
        )
    return rows


def main() -> None:
    data = _build_data()
    model = ProbabilisticTransitionEnsemble(heads=9, minimum_samples=180, seed=2700)
    report = model.fit(data)
    forecast = model.predict(data[-1].state)
    gate = evaluate_learning_gate(model, drift_detected=False)
    payload = {
        "version": forecast.model_version,
        "samples": forecast.sample_count,
        "calibration": {
            "train_rows": report.train_rows,
            "calibration_rows": report.calibration_rows,
            "evaluation_rows": report.evaluation_rows,
            "mean_interval_coverage": report.mean_interval_coverage,
            "calibrated": report.calibrated,
            "targets": [
                {
                    "target": row.target,
                    "rmse": row.rmse,
                    "interval_coverage": row.interval_coverage,
                    "conformal_radius": row.conformal_radius,
                }
                for row in report.targets
            ],
        },
        "forecast": {
            "next_return_mean": forecast.next_return_mean,
            "next_return_low": forecast.next_return_low,
            "next_return_high": forecast.next_return_high,
            "next_order_flow_mean": forecast.next_order_flow_mean,
            "next_liquidity_change_mean": forecast.next_liquidity_change_mean,
        },
        "gate": {
            "research_ready": gate.research_ready,
            "reasons": gate.reasons,
            "paper_trading_authority": gate.paper_trading_authority,
            "live_trading_authority": gate.live_trading_authority,
        },
    }
    if payload["gate"]["paper_trading_authority"] or payload["gate"]["live_trading_authority"]:
        raise RuntimeError("V8.1 authority invariant violated")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
