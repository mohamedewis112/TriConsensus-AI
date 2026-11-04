from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import time

import httpx
import numpy as np

from .chronos import MarketState
from .chronos_hybrid import ChronosArtificialMarketCivilization
from .chronos_dual_clock import head_specific_trust
from .chronos_learning import transitions_from_states
from .chronos_memory import MarketMemoryIndex
from .v6_microstructure import depth_metrics


OKX = "https://www.okx.com"
COINBASE = "https://api.exchange.coinbase.com"


class PublicLiveShadowFailure(RuntimeError):
    pass


async def _get_json(client: httpx.AsyncClient, url: str, params: dict | None = None):
    response = await client.get(url, params=params or {})
    response.raise_for_status()
    return response.json()


def _notional_depth_metrics(
    bids: list[list[str]],
    asks: list[list[str]],
    *,
    base_units_per_size: float,
) -> tuple[float, float, float, float]:
    if not bids or not asks:
        raise PublicLiveShadowFailure("empty orderbook side")
    if base_units_per_size <= 0:
        raise PublicLiveShadowFailure("invalid base_units_per_size")
    bid_notional = sum(
        float(price) * float(size) * base_units_per_size
        for price, size, *_ in bids
    )
    ask_notional = sum(
        float(price) * float(size) * base_units_per_size
        for price, size, *_ in asks
    )
    total = bid_notional + ask_notional
    imbalance = (bid_notional - ask_notional) / total if total > 0 else 0.0
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid = (best_bid + best_ask) / 2.0
    spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else 0.0
    return bid_notional, ask_notional, imbalance, spread_bps


async def _fetch_depth(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    okx_contract_value_base: float,
) -> dict:
    asset = symbol.removesuffix("USDT")
    okx_id = f"{asset}-USDT-SWAP"
    coinbase_id = f"{asset}-USD"
    okx_body, coinbase_ticker = await asyncio.gather(
        _get_json(
            client,
            f"{OKX}/api/v5/market/books",
            {"instId": okx_id, "sz": 20},
        ),
        _get_json(
            client,
            f"{COINBASE}/products/{coinbase_id}/ticker",
        ),
    )
    if (
        not isinstance(okx_body, dict)
        or str(okx_body.get("code", "")) != "0"
        or not okx_body.get("data")
    ):
        raise PublicLiveShadowFailure(
            f"OKX depth error: {okx_body.get('msg') if isinstance(okx_body, dict) else 'invalid response'}"
        )
    okx_book = okx_body["data"][0]
    o_bids = (okx_book.get("bids") or [])[:20]
    o_asks = (okx_book.get("asks") or [])[:20]
    if not o_bids or not o_asks:
        raise PublicLiveShadowFailure("empty OKX orderbook side")
    if not isinstance(coinbase_ticker, dict) or not coinbase_ticker.get("price"):
        raise PublicLiveShadowFailure("invalid Coinbase spot ticker")

    okx_metrics = _notional_depth_metrics(
        o_bids,
        o_asks,
        base_units_per_size=okx_contract_value_base,
    )
    okx_mid = (float(o_bids[0][0]) + float(o_asks[0][0])) / 2.0
    coinbase_mid = float(coinbase_ticker["price"])
    return {
        "okx": okx_metrics,
        "okx_mid": okx_mid,
        "coinbase_mid": coinbase_mid,
        "basis_bps": (
            (okx_mid - coinbase_mid)
            / max((okx_mid + coinbase_mid) / 2.0, 1e-9)
        )
        * 10_000.0,
    }


async def _fetch_context(client: httpx.AsyncClient, symbol: str) -> dict:
    asset = symbol.removesuffix("USDT")
    okx_id = f"{asset}-USDT-SWAP"
    candles_body, funding_body, oi_body, instrument_body = await asyncio.gather(
        _get_json(
            client,
            f"{OKX}/api/v5/market/candles",
            {"instId": okx_id, "bar": "1m", "limit": 10},
        ),
        _get_json(
            client,
            f"{OKX}/api/v5/public/funding-rate",
            {"instId": okx_id},
        ),
        _get_json(
            client,
            f"{OKX}/api/v5/public/open-interest",
            {"instType": "SWAP", "instId": okx_id},
        ),
        _get_json(
            client,
            f"{OKX}/api/v5/public/instruments",
            {"instType": "SWAP", "instId": okx_id},
        ),
    )
    for name, body in (
        ("candles", candles_body),
        ("funding", funding_body),
        ("open_interest", oi_body),
        ("instrument", instrument_body),
    ):
        if (
            not isinstance(body, dict)
            or str(body.get("code", "")) != "0"
            or not body.get("data")
        ):
            raise PublicLiveShadowFailure(
                f"OKX {name} error: {body.get('msg') if isinstance(body, dict) else 'invalid response'}"
            )

    rows = candles_body["data"]
    closed = sorted(
        [row for row in rows if len(row) < 9 or str(row[8]) == "1"],
        key=lambda row: int(row[0]),
    )
    if len(closed) < 6:
        raise PublicLiveShadowFailure("insufficient closed OKX 1m candles")
    close_1m = float(closed[-1][4])
    close_5m = float(closed[-5][4])
    funding_rate = float(funding_body["data"][0].get("fundingRate") or 0.0)
    open_interest = float(oi_body["data"][0].get("oi") or 0.0)
    instrument = instrument_body["data"][0]
    contract_type = str(instrument.get("ctType") or "")
    contract_value_ccy = str(instrument.get("ctValCcy") or "")
    contract_value = float(instrument.get("ctVal") or 0.0)
    if contract_type != "linear":
        raise PublicLiveShadowFailure(
            f"unsupported OKX contract type for {okx_id}: {contract_type}"
        )
    if contract_value_ccy != asset or contract_value <= 0:
        raise PublicLiveShadowFailure(
            f"ambiguous OKX contract value for {okx_id}: "
            f"ctVal={contract_value} ctValCcy={contract_value_ccy}"
        )
    return {
        "funding_rate": funding_rate,
        "open_interest": open_interest,
        "okx_contract_value_base": contract_value,
        "close_1m": close_1m,
        "close_5m": close_5m,
        "captured_ms": int(time.time() * 1000),
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
                self.errors.append(
                    f"context:{symbol}:{type(result).__name__}:{result}"
                )
                continue
            self.context[symbol] = result

    async def _capture_symbol(self, client: httpx.AsyncClient, symbol: str) -> None:
        context = self.context.get(symbol)
        if context is None:
            raise PublicLiveShadowFailure("missing public market context")

        depth = await _fetch_depth(
            client,
            symbol,
            okx_contract_value_base=float(context["okx_contract_value_base"]),
        )
        okx_bid, okx_ask, okx_imb, okx_spread = depth["okx"]
        mid = float(depth["okx_mid"])

        mids = self.mid_history[symbol]
        previous_mid = mids[-1] if mids else mid
        micro_return = (mid / previous_mid - 1.0) if previous_mid > 0 else 0.0
        mids.append(mid)
        if len(mids) > 180:
            del mids[:-180]

        prices = np.asarray(mids[-61:], dtype=float)
        if len(prices) >= 11:
            relative = np.diff(prices) / np.where(prices[:-1] == 0, 1.0, prices[:-1])
            realized = float(np.std(relative, ddof=1))
        else:
            realized = abs(micro_return)

        # Use one homogeneous L2 venue for depth/order-flow. Coinbase is a
        # lightweight spot reference for basis only; mixing unlike depth books
        # would create a unit/coverage artifact.
        bid_depth = float(okx_bid)
        ask_depth = float(okx_ask)
        weighted_imbalance = float(okx_imb)
        weighted_spread = float(okx_spread)

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
            open_interest_change=max(-1.0, min(1.0, oi_change)),
            liquidation_imbalance=0.0,
            cross_venue_basis_bps=float(depth["basis_bps"]),
            cross_venue_latency_ms=0.0,
            stablecoin_flow_z=0.0,
            dealer_gamma_z=0.0,
            mempool_pressure_z=0.0,
            crowding_score=0.0,
        )
        self.states[symbol].append(state)

    async def collect(
        self,
        *,
        seconds: int = 220,
        interval_seconds: float = 1.0,
    ) -> None:
        if seconds < 190:
            raise ValueError("public live shadow needs at least 190 seconds")
        timeout = httpx.Timeout(8.0)
        limits = httpx.Limits(max_connections=12, max_keepalive_connections=8)
        headers = {"User-Agent": "TriConsensus-Research/1.0"}
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            headers=headers,
        ) as client:
            await self._refresh_context(client)
            start = time.monotonic()
            sample = 0
            while time.monotonic() - start < seconds:
                tick_start = time.monotonic()
                if sample % 15 == 0:
                    await self._refresh_context(client)
                results = await asyncio.gather(
                    *[
                        self._capture_symbol(client, symbol)
                        for symbol in self.symbols
                    ],
                    return_exceptions=True,
                )
                for symbol, result in zip(self.symbols, results):
                    if isinstance(result, Exception):
                        self.errors.append(
                            f"depth:{symbol}:{type(result).__name__}:{result}"
                        )
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
    if (
        len(rows) >= 3
        and float(np.std(actual)) > 1e-12
        and float(np.std(predicted)) > 1e-12
    ):
        correlation = float(np.corrcoef(actual, predicted)[0, 1])
    return {
        "rows": len(rows),
        "directional_accuracy": float(
            np.mean(np.sign(actual) == np.sign(predicted))
        ),
        "mae": float(np.mean(np.abs(actual - predicted))),
        "rmse": float(np.sqrt(np.mean((actual - predicted) ** 2))),
        "interval_coverage": float(np.mean((actual >= low) & (actual <= high))),
        "correlation": correlation,
        "actual_mean": float(np.mean(actual)),
        "predicted_mean": float(np.mean(predicted)),
    }


def analyze_symbol(
    states: list[MarketState],
    symbol: str,
    *,
    universes: int = 100_000,
) -> dict:
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
            "predictive_skill": calibration.predictive_skill,
            "next_return_direction_deadzone": calibration.next_return_direction_deadzone,
            "next_return_directional_sample_count": calibration.next_return_directional_sample_count,
            "next_return_directional_accuracy": calibration.next_return_directional_accuracy,
            "next_return_directional_wilson_lower_95": calibration.next_return_directional_wilson_lower_95,
            "next_return_correlation": calibration.next_return_correlation,
            "next_return_actual_mean": calibration.next_return_actual_mean,
            "next_return_predicted_mean": calibration.next_return_predicted_mean,
            "next_return_mean_sign_agreement": calibration.next_return_mean_sign_agreement,
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
        "version": "v8.19-fast-head-live-shadow-0",
        "captured_at": datetime.now(UTC).isoformat(),
        "source": "PUBLIC_LIVE_OKX_L2_COINBASE_SPOT_REFERENCE",
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
        "collector_errors": collector.errors[-30:],
        "symbols": [],
    }
    for symbol in collector.symbols:
        report["symbols"].append(
            analyze_symbol(collector.states[symbol], symbol)
        )

    path = Path("artifacts/chronos-v89-public-live-shadow.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
