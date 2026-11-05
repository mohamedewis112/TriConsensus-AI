from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import numpy as np

from .chronos_immediate_ab import evaluate as evaluate_timing_ab
from .chronos_public_live import OKX, PublicLiveShadowCollector
from .regimes import classify_regime, fit_regime_thresholds
from .v7_signal_lab import build_v7_price_frame, generate_price_candidates


TARGET_SPECIES = "trend_breakout"
TARGET_SIDE = "SHORT"
TARGET_REGIME = "trend_down|mid_vol|momentum_mixed"
OKX_INST_ID = "ETH-USDT-SWAP"


class RegimeGateFailure(RuntimeError):
    pass


async def _fetch_okx_15m_candles(limit: int = 700) -> list[dict]:
    if limit < 220:
        raise ValueError("need at least 220 candles for regime calibration")

    rows_by_start: dict[int, list] = {}
    cursor: str | None = None
    timeout = httpx.Timeout(8.0)
    headers = {"User-Agent": "TriConsensus-Research/1.0"}

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        while len(rows_by_start) < limit:
            params = {
                "instId": OKX_INST_ID,
                "bar": "15m",
                "limit": "100",
            }
            if cursor is not None:
                params["after"] = cursor
                endpoint = f"{OKX}/api/v5/market/history-candles"
            else:
                endpoint = f"{OKX}/api/v5/market/candles"

            response = await client.get(endpoint, params=params)
            response.raise_for_status()
            body = response.json()
            if str(body.get("code", "")) != "0":
                raise RegimeGateFailure(f"OKX candles error: {body.get('msg')}")
            batch = body.get("data") or []
            if not batch:
                break

            for row in batch:
                if len(row) >= 9 and str(row[8]) != "1":
                    continue
                rows_by_start[int(row[0])] = row

            oldest = min(int(row[0]) for row in batch)
            next_cursor = str(oldest)
            if next_cursor == cursor:
                break
            cursor = next_cursor
            if len(batch) < 100:
                break

    ordered = [rows_by_start[key] for key in sorted(rows_by_start)]
    if len(ordered) < 220:
        raise RegimeGateFailure(
            f"insufficient closed OKX 15m candles: {len(ordered)}"
        )

    ordered = ordered[-limit:]
    return [
        {
            "start_ms": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "turnover": float(row[7]) if len(row) > 7 else 0.0,
        }
        for row in ordered
    ]


def _latest_gate_from_candles(candles: list[dict]) -> dict:
    frame = build_v7_price_frame(candles)
    required = (
        "close",
        "ema_20",
        "ema_50",
        "atr_14",
        "adx_14",
        "realized_vol_20",
        "macd_hist",
        "roc_10",
        "rsi_14",
        "volume_z_20",
        "support_20",
        "resistance_20",
        "return_1",
        "return_4",
        "return_16",
        "ema_gap_atr",
        "bb_z_20",
        "stoch_k",
        "atr_normalized_move",
        "breakout_up_distance_atr",
        "breakout_down_distance_atr",
    )
    eligible = [
        index
        for index in range(60, len(candles))
        if all(np.isfinite(float(frame.iloc[index][column])) for column in required)
    ]
    if len(eligible) < 120:
        raise RegimeGateFailure(
            f"insufficient eligible V7 rows: {len(eligible)}"
        )

    calibration_count = min(480, max(120, len(eligible) - 1))
    thresholds = fit_regime_thresholds(
        [
            float(frame.iloc[index]["realized_vol_20"])
            for index in eligible[:calibration_count]
        ]
    )
    latest_index = eligible[-1]
    row = frame.iloc[latest_index]
    features = {column: float(row[column]) for column in required}
    regime = classify_regime(features, thresholds)
    candidates = generate_price_candidates(features, regime)

    exact = next(
        (
            candidate
            for candidate in candidates
            if candidate.species == TARGET_SPECIES
            and candidate.side == TARGET_SIDE
            and candidate.regime_key == TARGET_REGIME
        ),
        None,
    )
    return {
        "matched": exact is not None,
        "target": {
            "species": TARGET_SPECIES,
            "side": TARGET_SIDE,
            "regime_key": TARGET_REGIME,
        },
        "latest_closed_candle_start_ms": int(candles[latest_index]["start_ms"]),
        "latest_regime_key": regime.key,
        "latest_candidates": [asdict(candidate) for candidate in candidates],
        "matched_candidate": asdict(exact) if exact else None,
        "calibration_rows": calibration_count,
        "eligible_rows": len(eligible),
        "candle_rows": len(candles),
    }


async def current_gate() -> dict:
    return _latest_gate_from_candles(await _fetch_okx_15m_candles())


async def run_regime_gated_ab() -> dict:
    before = await current_gate()
    report = {
        "version": "v8.26-regime-gated-eth-timing-ab-0",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "OKX_15M_REGIME_GATE_PLUS_OKX_L2_COINBASE_REFERENCE",
        "gate_before": before,
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }

    if not before["matched"]:
        report["verdict"] = "NO_MATCH_NO_AB_TEST"
        report["ab"] = None
        return report

    collector = PublicLiveShadowCollector(symbols=("ETHUSDT",))
    await collector.collect(seconds=235, interval_seconds=1.0)
    after = await current_gate()
    report["gate_after"] = after
    report["live_states"] = len(collector.states["ETHUSDT"])
    report["collector_errors"] = collector.errors[-20:]

    if not after["matched"]:
        report["verdict"] = "GATE_CHANGED_DURING_CAPTURE_EPISODE_REJECTED"
        report["ab"] = None
        return report

    result = evaluate_timing_ab(
        collector.states["ETHUSDT"],
        training_states=190,
    )
    result["slow_regime_asserted_current"] = True
    report["ab"] = result
    improvements = [
        row["improvement_per_opportunity"]
        for row in result["horizons"]
    ]
    report["verdict"] = (
        "REGIME_MATCH_TIMING_HELPED_THIS_EPISODE"
        if sum(value > 0 for value in improvements) >= 2
        else "REGIME_MATCH_TIMING_DID_NOT_HELP_THIS_EPISODE"
    )
    return report


async def main() -> None:
    report = await run_regime_gated_ab()
    path = Path("artifacts/chronos-v826-regime-gated-ab.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
