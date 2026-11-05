from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Sequence

import numpy as np

from .chronos import MarketState
from .chronos_learning import (
    ChronosTransition,
    LearningCalibrationReport,
    ProbabilisticTransitionEnsemble,
    transitions_from_states,
)
from .db import SupabaseRepository
from .market_data import BybitPublicClient, MarketDataFailure


CHRONOS_REPLAY_VERSION = "v8.3-chronos-real-replay-0"


class ChronosReplayFailure(RuntimeError):
    pass


def _display_symbol(symbol: str) -> str:
    normalized = symbol.upper().replace("/", "").replace("-", "")
    mapping = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT"}
    if normalized not in mapping:
        raise ValueError("unsupported CHRONOS replay symbol")
    return mapping[normalized]


def _exchange_symbol(symbol: str) -> str:
    return _display_symbol(symbol).replace("/", "")


def minute_bucket(timestamp_ms: int) -> int:
    return int(timestamp_ms // 60_000) * 60_000


@dataclass(frozen=True)
class ReplayCoverage:
    requested_micro_rows: int
    cross_venue_minutes: int
    candle_minutes: int
    aligned_states: int
    transitions: int
    alignment_ratio: float


@dataclass(frozen=True)
class ReplayTrainingResult:
    version: str
    symbol: str
    coverage: ReplayCoverage
    calibration: LearningCalibrationReport
    sample_count: int
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def _fuse_minute_rows(rows: Sequence[dict]) -> dict | None:
    latest: dict[str, dict] = {}
    for row in rows:
        venue = str(row.get("venue") or "")
        if venue not in {"bybit", "binance"}:
            continue
        ts = int(row.get("received_timestamp_ms") or 0)
        current = latest.get(venue)
        if current is None or ts > int(current.get("received_timestamp_ms") or 0):
            latest[venue] = row
    if set(latest) != {"bybit", "binance"}:
        return None
    bybit = latest["bybit"]
    binance = latest["binance"]
    receive_times = [
        int(bybit["received_timestamp_ms"]),
        int(binance["received_timestamp_ms"]),
    ]
    freshness = []
    max_received = max(receive_times)
    for ts in receive_times:
        freshness.append(1.0 / (1.0 + max(0, max_received - ts) / 1_000.0))
    weight_sum = sum(freshness)
    imbalance = sum(
        weight * float(row["imbalance"])
        for weight, row in zip(freshness, (bybit, binance))
    ) / weight_sum
    spread = sum(
        weight * float(row["spread_bps"])
        for weight, row in zip(freshness, (bybit, binance))
    ) / weight_sum
    return {
        "bid_depth": float(bybit["bid_depth"]) + float(binance["bid_depth"]),
        "ask_depth": float(bybit["ask_depth"]) + float(binance["ask_depth"]),
        "imbalance": max(-1.0, min(1.0, float(imbalance))),
        "spread_bps": max(0.0, float(spread)),
        "receive_skew_ms": max(receive_times) - min(receive_times),
        "received_timestamp_ms": max_received,
    }


def states_from_real_replay(
    *,
    symbol: str,
    microstructure_rows: Sequence[dict],
    candles_1m: Sequence[dict],
) -> tuple[list[MarketState], ReplayCoverage]:
    display = _display_symbol(symbol)
    exchange = _exchange_symbol(symbol)
    micro_by_minute: dict[int, list[dict]] = {}
    for row in microstructure_rows:
        if str(row.get("symbol")) not in {display, exchange}:
            continue
        ts = int(row.get("received_timestamp_ms") or 0)
        if ts <= 0:
            continue
        micro_by_minute.setdefault(minute_bucket(ts), []).append(row)

    fused = {
        bucket: value
        for bucket, rows in micro_by_minute.items()
        if (value := _fuse_minute_rows(rows)) is not None
    }
    candles = {
        minute_bucket(int(row["start_ms"])): row
        for row in candles_1m
        if float(row.get("close") or 0) > 0
    }
    ordered_buckets = sorted(set(fused) & set(candles))
    states: list[MarketState] = []
    closes: list[float] = []
    return_history: list[float] = []

    for bucket in ordered_buckets:
        candle = candles[bucket]
        close = float(candle["close"])
        previous = closes[-1] if closes else None
        ret1 = (close / previous - 1.0) if previous and previous > 0 else 0.0
        ret5 = 0.0
        if len(closes) >= 5 and closes[-5] > 0:
            ret5 = close / closes[-5] - 1.0
        if previous and previous > 0:
            return_history.append(ret1)
        realized = (
            float(np.std(return_history[-20:], ddof=1))
            if len(return_history[-20:]) >= 5
            else 0.0
        )
        micro = fused[bucket]
        states.append(
            MarketState(
                symbol=exchange,
                timestamp_ms=bucket,
                mid_price=close,
                return_1m=ret1,
                return_5m=ret5,
                realized_volatility=max(0.0, realized),
                bid_depth=float(micro["bid_depth"]),
                ask_depth=float(micro["ask_depth"]),
                order_flow_imbalance=float(micro["imbalance"]),
                spread_bps=float(micro["spread_bps"]),
                funding_rate=0.0,
                open_interest_change=0.0,
                liquidation_imbalance=0.0,
                cross_venue_basis_bps=0.0,
                cross_venue_latency_ms=float(micro["receive_skew_ms"]),
                stablecoin_flow_z=0.0,
                dealer_gamma_z=0.0,
                mempool_pressure_z=0.0,
                crowding_score=0.0,
            )
        )
        closes.append(close)

    transitions = transitions_from_states(states)
    denominator = max(1, min(len(fused), len(candles)))
    coverage = ReplayCoverage(
        requested_micro_rows=len(microstructure_rows),
        cross_venue_minutes=len(fused),
        candle_minutes=len(candles),
        aligned_states=len(states),
        transitions=len(transitions),
        alignment_ratio=len(states) / denominator,
    )
    return states, coverage


class ChronosRealReplaySource:
    """Read existing V6 evidence + public Bybit candles without writing trading data."""

    def __init__(
        self,
        repo: SupabaseRepository,
        market: BybitPublicClient,
    ):
        self.repo = repo
        self.market = market

    def load_microstructure_rows(
        self,
        *,
        symbol: str,
        hours: int = 72,
        page_size: int = 1000,
        max_rows: int = 20_000,
    ) -> list[dict]:
        if hours < 3 or hours > 24 * 14:
            raise ValueError("hours outside [3,336]")
        display = _display_symbol(symbol)
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        rows: list[dict] = []
        offset = 0
        while len(rows) < max_rows:
            try:
                batch = (
                    self.repo.client.table("v6_microstructure_snapshots")
                    .select(
                        "symbol,venue,captured_at,exchange_timestamp_ms,"
                        "received_timestamp_ms,sequence,bid_depth,ask_depth,"
                        "imbalance,spread_bps"
                    )
                    .eq("symbol", display)
                    .gte("captured_at", cutoff.isoformat())
                    .order("received_timestamp_ms")
                    .range(offset, offset + page_size - 1)
                    .execute()
                    .data
                )
            except Exception as exc:
                raise ChronosReplayFailure(f"microstructure replay read failed: {exc}") from exc
            batch = batch or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return rows[:max_rows]

    async def build(
        self,
        *,
        symbol: str,
        hours: int = 72,
    ) -> tuple[list[ChronosTransition], ReplayCoverage]:
        micro = self.load_microstructure_rows(symbol=symbol, hours=hours)
        if not micro:
            raise ChronosReplayFailure("no V6 microstructure history available")
        timestamps = [int(row["received_timestamp_ms"]) for row in micro]
        start_ms = minute_bucket(min(timestamps))
        end_ms = minute_bucket(max(timestamps)) + 60_000
        try:
            candles = await self.market.historical_klines(
                _exchange_symbol(symbol),
                "1m",
                start_ms=start_ms,
                end_ms=end_ms,
            )
        except MarketDataFailure as exc:
            raise ChronosReplayFailure(f"public 1m candle replay failed: {exc}") from exc
        states, coverage = states_from_real_replay(
            symbol=symbol,
            microstructure_rows=micro,
            candles_1m=candles,
        )
        transitions = transitions_from_states(states)
        if len(transitions) < 180:
            raise ChronosReplayFailure(
                f"insufficient aligned real transitions: {len(transitions)} < 180"
            )
        if coverage.alignment_ratio < 0.70:
            raise ChronosReplayFailure(
                f"real replay alignment too sparse: {coverage.alignment_ratio:.3f}"
            )
        return transitions, coverage


async def train_real_replay_model(
    source: ChronosRealReplaySource,
    *,
    symbol: str,
    hours: int = 72,
    model: ProbabilisticTransitionEnsemble | None = None,
) -> tuple[ProbabilisticTransitionEnsemble, ReplayTrainingResult]:
    transitions, coverage = await source.build(symbol=symbol, hours=hours)
    model = model or ProbabilisticTransitionEnsemble(
        heads=9,
        minimum_samples=180,
        seed=2700,
    )
    calibration = model.fit(transitions)
    result = ReplayTrainingResult(
        version=CHRONOS_REPLAY_VERSION,
        symbol=_display_symbol(symbol),
        coverage=coverage,
        calibration=calibration,
        sample_count=len(transitions),
    )
    return model, result
