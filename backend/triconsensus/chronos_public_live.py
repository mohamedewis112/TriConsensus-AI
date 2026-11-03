from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import time

import httpx
import numpy as np

from .chronos import MarketState
from .chronos_hybrid import ChronosArtificialMarketCivilization
from .chronos_learning import transitions_from_states
from .chronos_memory import MarketMemoryIndex
from .v6_microstructure import depth_metrics


BYBIT = "https://api.bytick.com"
BINANCE = "https://fapi.binance.com"


class PublicLiveShadowFailure(RuntimeError):
    pass


def _bybit_result(body: dict) -> dict:
    if body.get("retCode") != 0:
        raise PublicLiveShadowFailure(f"Bybit error: {body.get('retMsg')}")
    return body.get("result") or {}


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


async def _fetch_depth(client: httpx.AsyncClient, symbol: str) -> dict:
    bybit_body, binance_body = await asyncio.gather(
        _get_json(
            client,
            f"{BYBIT}/v5/market/orderbook",
            {"category": "linear", "symbol": symbol, "limit": 50},
        ),
        _get_json(
            client,
            f"{BINANCE}/fapi/v1/depth",
            {"symbol": symbol, "limit": 20},
        ),
    )
    bybit = _bybit_result(bybit_body)
    b_bids = bybit.get("b") or []
    b_asks = bybit.get("a") or []
    n_bids = binance_body.get("bids") or []
    n_asks = binance_body.get("asks") or []
    bybit_metrics = depth_metrics(b_bids, b_asks)
    binance_metrics = depth_metrics(n_bids, n_asks)
    bybit_mid = (float(b_bids[0][0]) + float(b_asks[0][0])) / 2.0
    binance_mid = (float(n_bids[0][0]) + float(n_asks[0][0])) / 2.0
    return {
        "bybit": bybit_metrics,
        "binance": binance_metrics,
        "bybit_mid": bybit_mid,
        "binance_mid": binance_mid,
        "basis_bps": ((bybit_mid - binance_mid) / ((bybit_mid + binance_mid) / 2.0)) * 10_000,
    }


async def _fetch_context(client: httpx.AsyncClient, symbol: str) -> dict:
    ticker_body, kline_body = await asyncio.gather(
        _get_json(
            client,
            f"{BYBIT}/v5/market/tickers",
            {"category": "linear", "symbol": symbol},
        ),
        _get_json(
            client,
            f"{BYBIT}/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": "1", "limit": 10},
        ),
    )
    ticker_rows = _bybit_result(ticker_body).get("list") or []
    kline_rows = _bybit_result(kline_body).get("list") or []
    if not ticker_rows or len(kline_rows) < 7:
        raise PublicLiveShadowFailure("insufficient public ticker/kline context")
    ticker = ticker_rows[0]
    now_ms = int(time.time() * 1000)
    closed = sorted(
        [
            row
            for row in kline_rows
            if int(row[0]) <= now_ms - 60_000
        ],
        key=lambda row: int(row[0]),
    )
    if len(closed) < 6:
        raise PublicLiveShadowFailure("insufficient closed 1m candles")
    close_1m = float(closed[-1][4])
    close_5m = float(closed[-5][4])
    return {
        "funding_rate": float(ticker.get("fundingRate") or 0.0),
        "open_interest": float(ticker.get("openInterest") or 0.0),
        "close_1m": close_1m,
        "close_5m": close_5m,
        "captured_ms": now_ms,
    }


class PublicLiveShadowCollector:
    def __init__(self, symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")):
        self.symbols = symbols
        self.context: dict[str, dict] = {}
        self.last_open_interest: dict[str, float] = {}
        self.mid_history: dict[str, list[float]] = {symbol: [] for symbol in symbols}
        self.states: dict[str, list[MarketState]] = {symbol: [] for symbol in symbols}
        self.errors: list[str] = []

    async def _refresh_context(self, client: httpx.AsyncClient) -> None:
        results = await asyncio.gather(
            *[_fetch_context(client, symbol) for symbol in self.symbols],
            return_exceptions=True,
        )
        for symbol, result in zip(self.symbols, results):
            if isinstance(result, Exception):
                self.errors.append(f"context:{symbol}:{type(result).__name__}:{result}")
                continue
            self.context[symbol] = result

    async def _capture_symbol(self, client: httpx.AsyncClient, symbol: str) -> None:
        context = self.context.get(symbol)
        if context is None:
            raise PublicLiveShadowFailure("missing public market context")
        depth = await _fetch_depth(client, symbol)
        bybit_bid, bybit_ask, bybit_imb, bybit_spread = depth["bybit"]
        bin_bid, bin_ask, bin_imb, bin_spread = depth["binance"]
        mid = float(depth["bybit_mid"])
        mids = self.mid_history[symbol]
        previous_mid = mids[-1] if mids else mid
        micro_return = (mid / previous_mid - 1.0) if previous_mid > 0 else 0.0
        mids.append(mid)
        if len(mids) > 180:
            del mids[:-180]
        recent_returns = np.diff(np.asarray(mids[-61:], dtype=float))
        recent_base = np.asarray(mids[-61:-1], dtype=float)
        if len(recent_returns) >= 10 and len(recent_base) == len(recent_returns):
            relative = recent_returns / np.where(recent_base == 0, 1.0, recent_base)
            realized = float(np.std(relative, ddof=1))
        else:
            realized = abs(micro_return)

        bid_depth = float(bybit_bid + bin_bid)
        ask_depth = float(bybit_ask + bin_ask)
        weighted_imbalance = float(
            (bybit_imb * (bybit_bid + bybit_ask) + bin_imb * (bin_bid + bin_ask))
            / max(bybit_bid + bybit_ask + bin_bid + bin_ask, 1e-9)
        )
        weighted_spread = float((bybit_spread + bin_spread) / 2.0)
        oi = float(context["open_interest"])
        prior_oi = self.last_open_interest.get(symbol, oi)
        oi_change = (oi / prior_oi - 1.0) if prior_oi > 0 else 0.0
        self.last_open_interest[symbol] = oi

        state = MarketState(
            symbol=symbol,
            timestamp_ms=int(time.time() * 1000),
            mid_price=mid,
            return_1m=(mid / float(context["close_1m"]) - 1.0),
            return_5m=(mid / float(context["close_5m"]) - 1.0),
            realized_volatility=max(0.0, realized),
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            order_flow_imbalance=max(-1.0, min(1.0, weighted_imbalance)),
            spread_bps=max(0.0, weighted_spread),
            funding_rate=float(context["funding_rate"]),
            open_interest_change=float(max(-1.0, min(1.0, oi_change))),
            liquidation_imbalance=0.0,
            cross_venue_basis_bps=float(depth["basis_bps"]),
            cross_venue_latency_ms=0.0,
            stablecoin_flow_z=0.0,
            dealer_gamma_z=0.0,
            mempool_pressure_z=0.0,
            crowding_score=0.0,
        )
        self.states[symbol].append(state)

    async def collect(self, *, seconds: int = 220, interval_seconds: float = 1.0) -> None:
        if seconds < 190:
            raise ValueError("public live shadow needs at least 190 seconds")
        timeout = httpx.Timeout(8.0)
        limits = httpx.Limits(max_connections=12, max_keepalive_connections=8)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            await self._refresh_context(client)
            start = time.monotonic()
            sample = 0
            while time.monotonic() - start < seconds:
                tick_start = time.monotonic()
                if sample % 15 == 0:
                    await self._refresh_context(client)
                results = await asyncio.gather(
                    *[self._capture_symbol(client, symbol) for symbol in self.symbols],
                    return_exceptions=True,
                )
                for symbol, result in zip(self.symbols, results):
                    if isinstance(result, Exception):
                        self.errors.append(f"depth:{symbol}:{type(result).__name__}:{result}")
                sample += 1
                delay = interval_seconds - (time.monotonic() - tick_start)
                if delay > 0:
                    await asyncio.sleep(delay)


def _eval_metrics(model, transitions, evaluation_rows: int) -> dict:
    rows = list(transitions[-evaluation_rows:])
    preds = [model.predict(row.state) for row in rows]
    actual = np.asarray([row.next_return for row in rows], dtype=float)
    predicted = np.asarray([p.next_return_mean for p in preds], dtype=float)
    low = np.asarray([p.next_return_low for p in preds], dtype=float)
    high = np.asarray([p.next_return_high for p in preds], dtype=float)
    correlation = 0.0
    if len(rows) >= 3 and np.std(actual) > 1e-12 and np.std(predicted) > 1e-12:
        correlation = float(np.corrcoef(actual, predicted)[0, 1])
    return {
        "rows": len(rows),
        "directional_accuracy": float(np.mean(np.sign(actual) == np.sign(predicted))),
        "mae": float(np.mean(np.abs(actual - predicted))),
        "rmse": float(np.sqrt(np.mean((actual - predicted) ** 2))),
        "interval_coverage": float(np.mean((actual >= low) & (actual <= high))),
        "correlation": correlation,
        "actual_mean": float(np.mean(actual)),
        "predicted_mean": float(np.mean(predicted)),
    }


def analyze_symbol(states: list[MarketState], symbol: str, *, universes: int = 100_000) -> dict:
    transitions = transitions_from_states(states)
    if len(transitions) < 180:
        raise PublicLiveShadowFailure(
            f"{symbol} has only {len(transitions)} real transitions; need >=180"
        )
    civilization = ChronosArtificialMarketCivilization()
    civilization.fit(transitions)
    calibration = civilization.transition_model.calibration_report
    if calibration is None:
        raise PublicLiveShadowFailure("missing learning calibration")

    memory = MarketMemoryIndex(
        minimum_neighbors=10,
        similarity_floor=0.55,
        max_neighbors=30,
        recency_half_life_days=1.0,
    )
    memory.fit_transitions(transitions)
    query = transitions[-1].state
    recall = memory.recall(query)
    forecast = civilization.forecast(
        query,
        drift_detected=False,
        universes=universes,
        horizon_steps=12,
        seed=2700,
    )
    return {
        "symbol": symbol,
        "real_states": len(states),
        "real_transitions": len(transitions),
        "calibration": {
            "train_rows": calibration.train_rows,
            "calibration_rows": calibration.calibration_rows,
            "evaluation_rows": calibration.evaluation_rows,
            "mean_interval_coverage": calibration.mean_interval_coverage,
            "calibrated": calibration.calibrated,
            "targets": [asdict(row) for row in calibration.targets],
        },
        "untouched_evaluation": _eval_metrics(
            civilization.transition_model,
            transitions,
            calibration.evaluation_rows,
        ),
        "market_memory": {
            "usable": recall.usable,
            "neighbors": recall.neighbor_count,
            "recurrence_score": recall.recurrence_score,
            "weighted_next_return": recall.weighted_next_return,
            "return_dispersion": recall.return_dispersion,
            "reason": recall.reason,
        },
        "civilization": {
            "world_mode": forecast.world_mode,
            "candidate": forecast.research_candidate,
            "dominant_actor_hypothesis": forecast.dominant_actor,
            "future": asdict(forecast.future),
            "trust": asdict(forecast.trust),
        },
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }


async def main() -> None:
    collector = PublicLiveShadowCollector()
    await collector.collect(seconds=220, interval_seconds=1.0)
    report = {
        "version": "v8.9-public-live-shadow-0",
        "captured_at": datetime.now(UTC).isoformat(),
        "source": "PUBLIC_LIVE_BYBIT_BINANCE",
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
        "collector_errors": collector.errors[-30:],
        "symbols": [],
    }
    for symbol in collector.symbols:
        report["symbols"].append(analyze_symbol(collector.states[symbol], symbol))
    path = Path("artifacts/chronos-v89-public-live-shadow.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
