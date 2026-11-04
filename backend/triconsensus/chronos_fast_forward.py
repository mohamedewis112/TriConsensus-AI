from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import time

import httpx
import numpy as np

from .chronos import MarketState
from .chronos_learning import ProbabilisticTransitionEnsemble, transitions_from_states
from .chronos_public_live import PublicLiveShadowCollector


CHRONOS_FAST_FORWARD_VERSION = "v8.20-fast-head-fresh-forward-0"


def _hash_payload(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class FastHeadReceipt:
    symbol: str
    timestamp_ms: int
    model_version: str
    predicted_return: float
    return_low: float
    return_high: float
    predicted_orderflow: float
    orderflow_low: float
    orderflow_high: float
    predicted_liquidity_change: float
    liquidity_low: float
    liquidity_high: float
    baseline_orderflow: float
    baseline_return: float
    baseline_liquidity_change: float
    entry_depth: float
    previous_hash: str | None
    receipt_hash: str
    research_only: bool = True


@dataclass(frozen=True)
class FastHeadResolution:
    receipt_hash: str
    symbol: str
    resolved_timestamp_ms: int
    actual_return: float
    actual_orderflow: float
    actual_liquidity_change: float
    research_only: bool = True


@dataclass(frozen=True)
class HeadForwardMetrics:
    target: str
    samples: int
    model_rmse: float
    baseline_rmse: float
    relative_skill: float
    interval_coverage: float
    correlation: float
    qualified: bool
    research_only: bool = True


@dataclass(frozen=True)
class FastHeadForwardReport:
    version: str
    symbol: str
    training_transitions: int
    resolved_forecasts: int
    receipt_chain_valid: bool
    price: HeadForwardMetrics
    orderflow: HeadForwardMetrics
    liquidity: HeadForwardMetrics
    fast_execution_evidence_qualified: bool
    reasons: tuple[str, ...]
    paper_trading_authority: bool = False
    live_trading_authority: bool = False
    research_only: bool = True


class FastHeadLedger:
    def __init__(self) -> None:
        self.receipts: list[FastHeadReceipt] = []
        self.resolutions: dict[str, FastHeadResolution] = {}

    def register(self, *, symbol: str, state: MarketState, forecast) -> FastHeadReceipt:
        previous = self.receipts[-1].receipt_hash if self.receipts else None
        body = {
            "symbol": symbol,
            "timestamp_ms": int(state.timestamp_ms),
            "model_version": forecast.model_version,
            "predicted_return": float(forecast.next_return_mean),
            "return_low": float(forecast.next_return_low),
            "return_high": float(forecast.next_return_high),
            "predicted_orderflow": float(forecast.next_order_flow_mean),
            "orderflow_low": float(forecast.next_order_flow_low),
            "orderflow_high": float(forecast.next_order_flow_high),
            "predicted_liquidity_change": float(forecast.next_liquidity_change_mean),
            "liquidity_low": float(forecast.next_liquidity_change_low),
            "liquidity_high": float(forecast.next_liquidity_change_high),
            "baseline_orderflow": float(state.order_flow_imbalance),
            "baseline_return": 0.0,
            "baseline_liquidity_change": 0.0,
            "entry_depth": float(max(state.total_depth, 1e-9)),
            "previous_hash": previous,
        }
        receipt = FastHeadReceipt(**body, receipt_hash=_hash_payload(body))
        self.receipts.append(receipt)
        return receipt

    def resolve(self, receipt: FastHeadReceipt, next_state: MarketState, entry_price: float) -> None:
        if receipt.receipt_hash in self.resolutions:
            raise ValueError("fast-head receipt already resolved")
        actual_return = (next_state.mid_price / entry_price) - 1.0
        actual_liquidity = (next_state.total_depth / receipt.entry_depth) - 1.0
        self.resolutions[receipt.receipt_hash] = FastHeadResolution(
            receipt_hash=receipt.receipt_hash,
            symbol=receipt.symbol,
            resolved_timestamp_ms=next_state.timestamp_ms,
            actual_return=float(actual_return),
            actual_orderflow=float(next_state.order_flow_imbalance),
            actual_liquidity_change=float(max(-1.0, min(1.0, actual_liquidity))),
        )

    def verify_chain(self) -> bool:
        previous = None
        for receipt in self.receipts:
            body = asdict(receipt)
            claimed = body.pop("receipt_hash")
            body.pop("research_only")
            if receipt.previous_hash != previous or _hash_payload(body) != claimed:
                return False
            previous = claimed
        return True

    @staticmethod
    def _metrics(
        *,
        target: str,
        actual: np.ndarray,
        predicted: np.ndarray,
        baseline: np.ndarray,
        low: np.ndarray,
        high: np.ndarray,
        minimum_samples: int = 50,
    ) -> HeadForwardMetrics:
        model_rmse = float(np.sqrt(np.mean((actual - predicted) ** 2)))
        baseline_rmse = float(np.sqrt(np.mean((actual - baseline) ** 2)))
        relative_skill = (
            1.0 - model_rmse / baseline_rmse
            if baseline_rmse > 1e-12
            else 0.0
        )
        coverage = float(np.mean((actual >= low) & (actual <= high)))
        correlation = 0.0
        if len(actual) >= 3 and np.std(actual) > 1e-12 and np.std(predicted) > 1e-12:
            correlation = float(np.corrcoef(actual, predicted)[0, 1])
        qualified = bool(
            len(actual) >= minimum_samples
            and relative_skill > 0.0
            and coverage >= 0.72
            and correlation > 0.0
        )
        return HeadForwardMetrics(
            target=target,
            samples=len(actual),
            model_rmse=model_rmse,
            baseline_rmse=baseline_rmse,
            relative_skill=float(relative_skill),
            interval_coverage=coverage,
            correlation=correlation,
            qualified=qualified,
        )

    def audit(self, *, symbol: str, training_transitions: int) -> FastHeadForwardReport:
        pairs = [
            (receipt, self.resolutions[receipt.receipt_hash])
            for receipt in self.receipts
            if receipt.receipt_hash in self.resolutions
        ]
        if not pairs:
            raise ValueError("no resolved fast-head forecasts")

        def arrays(receipt_attr: str, resolution_attr: str, baseline_attr: str, low_attr: str, high_attr: str):
            actual = np.asarray([getattr(resolution, resolution_attr) for _, resolution in pairs], dtype=float)
            predicted = np.asarray([getattr(receipt, receipt_attr) for receipt, _ in pairs], dtype=float)
            baseline = np.asarray([getattr(receipt, baseline_attr) for receipt, _ in pairs], dtype=float)
            low = np.asarray([getattr(receipt, low_attr) for receipt, _ in pairs], dtype=float)
            high = np.asarray([getattr(receipt, high_attr) for receipt, _ in pairs], dtype=float)
            return actual, predicted, baseline, low, high

        price = self._metrics(
            target="next_return",
            *arrays("predicted_return", "actual_return", "baseline_return", "return_low", "return_high"),
        )
        flow = self._metrics(
            target="next_orderflow",
            *arrays("predicted_orderflow", "actual_orderflow", "baseline_orderflow", "orderflow_low", "orderflow_high"),
        )
        liquidity = self._metrics(
            target="next_liquidity_change",
            *arrays("predicted_liquidity_change", "actual_liquidity_change", "baseline_liquidity_change", "liquidity_low", "liquidity_high"),
        )

        reasons: list[str] = []
        if not flow.qualified:
            reasons.append("orderflow_head_not_fresh_forward_qualified")
        if not liquidity.qualified:
            reasons.append("liquidity_head_not_fresh_forward_qualified")
        if not self.verify_chain():
            reasons.append("forecast_receipt_chain_invalid")

        return FastHeadForwardReport(
            version=CHRONOS_FAST_FORWARD_VERSION,
            symbol=symbol,
            training_transitions=training_transitions,
            resolved_forecasts=len(pairs),
            receipt_chain_valid=self.verify_chain(),
            price=price,
            orderflow=flow,
            liquidity=liquidity,
            fast_execution_evidence_qualified=not reasons,
            reasons=tuple(reasons),
        )


async def main() -> None:
    training_seconds = 200
    forward_seconds = 120

    collector = PublicLiveShadowCollector()
    await collector.collect(seconds=training_seconds, interval_seconds=1.0)

    models: dict[str, ProbabilisticTransitionEnsemble] = {}
    ledgers: dict[str, FastHeadLedger] = {}
    training_counts: dict[str, int] = {}
    pending: dict[str, tuple[FastHeadReceipt, float] | None] = {}

    for symbol in collector.symbols:
        transitions = transitions_from_states(collector.states[symbol])
        if len(transitions) < 180:
            raise RuntimeError(f"{symbol} insufficient training transitions: {len(transitions)}")
        model = ProbabilisticTransitionEnsemble(heads=9, minimum_samples=180, seed=2020)
        model.fit(transitions)
        models[symbol] = model
        ledgers[symbol] = FastHeadLedger()
        training_counts[symbol] = len(transitions)
        pending[symbol] = None

    timeout = httpx.Timeout(8.0)
    limits = httpx.Limits(max_connections=12, max_keepalive_connections=8)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": "TriConsensus-Research/1.0"},
    ) as client:
        await collector._refresh_context(client)
        start = time.monotonic()
        sample = 0
        while time.monotonic() - start < forward_seconds:
            tick = time.monotonic()
            if sample % 15 == 0:
                await collector._refresh_context(client)
            results = await asyncio.gather(
                *[collector._capture_symbol(client, symbol) for symbol in collector.symbols],
                return_exceptions=True,
            )
            for symbol, result in zip(collector.symbols, results):
                if isinstance(result, Exception):
                    collector.errors.append(f"fast_forward:{symbol}:{type(result).__name__}:{result}")
                    continue
                state = collector.states[symbol][-1]
                previous = pending[symbol]
                if previous is not None:
                    receipt, entry_price = previous
                    ledgers[symbol].resolve(receipt, state, entry_price)
                forecast = models[symbol].predict(state)
                receipt = ledgers[symbol].register(symbol=symbol, state=state, forecast=forecast)
                pending[symbol] = (receipt, state.mid_price)
            sample += 1
            delay = 1.0 - (time.monotonic() - tick)
            if delay > 0:
                await asyncio.sleep(delay)

    report = {
        "version": CHRONOS_FAST_FORWARD_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "PUBLIC_LIVE_OKX_L2_COINBASE_SPOT_REFERENCE",
        "training_seconds": training_seconds,
        "forward_seconds": forward_seconds,
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
        "collector_errors": collector.errors[-30:],
        "symbols": [],
    }
    for symbol in collector.symbols:
        audit = ledgers[symbol].audit(
            symbol=symbol,
            training_transitions=training_counts[symbol],
        )
        report["symbols"].append(asdict(audit))

    path = Path("artifacts/chronos-v820-fast-head-forward.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
