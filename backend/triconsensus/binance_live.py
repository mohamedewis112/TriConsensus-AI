from __future__ import annotations

from dataclasses import dataclass
import math
import time

import httpx


UM_METRICS_START_LABEL_MS = 1782345600000  # 2026-06-25T00:00:00Z
FIVE_MINUTES_MS = 5 * 60 * 1000


def normalize_global_ratio_timestamp(timestamp_ms: int) -> int:
    if timestamp_ms >= UM_METRICS_START_LABEL_MS:
        return timestamp_ms + FIVE_MINUTES_MS
    return timestamp_ms


class BinanceLiveContextFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class GlobalLongShortPoint:
    timestamp_ms: int
    ratio: float
    long_account: float
    short_account: float


@dataclass(frozen=True)
class V51LiveSignal:
    symbol: str
    signal: str
    feature_name: str
    feature_value: float
    context_timestamp_ms: int
    context_age_ms: int
    low_threshold: float
    high_threshold: float
    reason: str


def aggregate_5m_ratio_to_15m(
    points: list[GlobalLongShortPoint],
) -> list[GlobalLongShortPoint]:
    buckets: dict[int, list[GlobalLongShortPoint]] = {}
    for point in sorted(points, key=lambda item: item.timestamp_ms):
        bucket_end = (
            ((point.timestamp_ms - 1) // (15 * 60_000)) + 1
        ) * (15 * 60_000)
        buckets.setdefault(bucket_end, []).append(point)

    aggregated: list[GlobalLongShortPoint] = []
    for bucket_end in sorted(buckets):
        rows = buckets[bucket_end]
        expected = [
            bucket_end - 10 * 60_000,
            bucket_end - 5 * 60_000,
            bucket_end,
        ]
        timestamps = [row.timestamp_ms for row in rows]
        if timestamps != expected:
            continue
        last = rows[-1]
        aggregated.append(
            GlobalLongShortPoint(
                timestamp_ms=bucket_end,
                ratio=last.ratio,
                long_account=last.long_account,
                short_account=last.short_account,
            )
        )
    return aggregated


class BinanceGlobalRatioClient:
    def __init__(
        self,
        base_url: str = "https://fapi.binance.com",
        *,
        context_lag_minutes: int = 15,
        max_context_age_minutes: int = 45,
    ) -> None:
        if context_lag_minutes < 15:
            raise ValueError("V5.1 requires at least 15m context lag")
        if max_context_age_minutes <= context_lag_minutes:
            raise ValueError("max context age must exceed context lag")
        self.base_url = base_url.rstrip("/")
        self.context_lag_ms = context_lag_minutes * 60_000
        self.max_context_age_ms = max_context_age_minutes * 60_000

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("V5.1 supports BTCUSDT and ETHUSDT only")

    async def recent_15m(
        self,
        symbol: str,
        *,
        limit: int = 32,
    ) -> list[GlobalLongShortPoint]:
        self._validate_symbol(symbol)
        if not 15 <= limit <= 500:
            raise ValueError("raw 5m ratio limit must be between 15 and 500")

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=7.0),
                follow_redirects=True,
                headers={"User-Agent": "TriConsensus-AI/0.1 paper"},
            ) as client:
                response = await client.get(
                    f"{self.base_url}/futures/data/globalLongShortAccountRatio",
                    params={
                        "symbol": symbol,
                        "period": "5m",
                        "limit": limit,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BinanceLiveContextFailure(
                f"global ratio request failed: {type(exc).__name__}"
            ) from exc

        if not isinstance(payload, list):
            raise BinanceLiveContextFailure("unexpected global ratio payload")

        points: dict[int, GlobalLongShortPoint] = {}
        for row in payload:
            try:
                timestamp = normalize_global_ratio_timestamp(
                    int(row["timestamp"])
                )
                ratio = float(row["longShortRatio"])
                long_account = float(row["longAccount"])
                short_account = float(row["shortAccount"])
            except (KeyError, TypeError, ValueError) as exc:
                raise BinanceLiveContextFailure("invalid global ratio row") from exc

            if (
                timestamp <= 0
                or not all(
                    math.isfinite(value)
                    for value in (ratio, long_account, short_account)
                )
                or ratio <= 0
                or long_account < 0
                or short_account < 0
            ):
                raise BinanceLiveContextFailure("invalid global ratio values")

            points[timestamp] = GlobalLongShortPoint(
                timestamp_ms=timestamp,
                ratio=ratio,
                long_account=long_account,
                short_account=short_account,
            )

        raw = [points[key] for key in sorted(points)]
        ordered = aggregate_5m_ratio_to_15m(raw)
        if len(ordered) < 5:
            raise BinanceLiveContextFailure(
                "fewer than five complete aggregated 15m ratio observations"
            )
        return ordered

    def signal_from_points(
        self,
        symbol: str,
        points: list[GlobalLongShortPoint],
        *,
        low_threshold: float,
        high_threshold: float,
        now_ms: int | None = None,
    ) -> V51LiveSignal:
        self._validate_symbol(symbol)
        if not low_threshold < high_threshold:
            raise ValueError("invalid calibration thresholds")

        reference_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        cutoff_ms = reference_ms - self.context_lag_ms
        eligible = [point for point in points if point.timestamp_ms <= cutoff_ms]
        if len(eligible) < 5:
            raise BinanceLiveContextFailure(
                "insufficient completed lagged ratio observations"
            )

        latest = eligible[-1]
        one_hour_prior = eligible[-5]
        expected_delta = 4 * 15 * 60_000
        actual_delta = latest.timestamp_ms - one_hour_prior.timestamp_ms
        if actual_delta != expected_delta:
            raise BinanceLiveContextFailure("ratio series has a one-hour gap")

        recent = eligible[-5:]
        for left, right in zip(recent, recent[1:]):
            if right.timestamp_ms - left.timestamp_ms != 15 * 60_000:
                raise BinanceLiveContextFailure("ratio series is not contiguous 15m")

        context_age_ms = reference_ms - latest.timestamp_ms
        if context_age_ms < self.context_lag_ms:
            raise BinanceLiveContextFailure("context lag guard violated")
        if context_age_ms > self.max_context_age_ms:
            raise BinanceLiveContextFailure("global ratio context is stale")

        feature_value = math.log(latest.ratio) - math.log(one_hour_prior.ratio)
        if feature_value <= low_threshold:
            signal = "LONG"
            reason = "bottom_decile_inverse_signal"
        elif feature_value >= high_threshold:
            signal = "SHORT"
            reason = "top_decile_inverse_signal"
        else:
            signal = "NO_TRADE"
            reason = "inside_calibrated_deciles"

        return V51LiveSignal(
            symbol=symbol,
            signal=signal,
            feature_name="global_long_short_change_1h",
            feature_value=float(feature_value),
            context_timestamp_ms=latest.timestamp_ms,
            context_age_ms=context_age_ms,
            low_threshold=float(low_threshold),
            high_threshold=float(high_threshold),
            reason=reason,
        )

    async def signal(
        self,
        symbol: str,
        *,
        low_threshold: float,
        high_threshold: float,
        now_ms: int | None = None,
    ) -> V51LiveSignal:
        points = await self.recent_15m(symbol)
        return self.signal_from_points(
            symbol,
            points,
            low_threshold=low_threshold,
            high_threshold=high_threshold,
            now_ms=now_ms,
        )
