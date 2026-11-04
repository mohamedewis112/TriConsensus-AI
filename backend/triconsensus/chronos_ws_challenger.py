from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Sequence

import numpy as np

from .chronos_ws_nervous_system import (
    NervousAccumulator,
    NervousFeatureFrame,
    OKX_PUBLIC_WS,
)


CHRONOS_WS_CHALLENGER_VERSION = "v8.21-ws-fast-execution-challenger-0"

FEATURE_NAMES = (
    "spread_bps",
    "microprice_offset_bps",
    "imbalance_l1",
    "imbalance_l5",
    "book_pressure_slope",
    "trade_imbalance_1s",
    "trade_imbalance_5s",
    "trade_intensity_1s",
    "trade_intensity_5s",
    "signed_trade_contracts_1s_scaled",
    "signed_trade_contracts_5s_scaled",
)

TARGET_NAMES = (
    "future_trade_imbalance_1s",
    "future_spread_bps",
    "future_microprice_offset_bps",
)


@dataclass(frozen=True)
class WsSupervisedRow:
    timestamp_ms: int
    features: tuple[float, ...]
    targets: tuple[float, ...]
    baselines: tuple[float, ...]


@dataclass(frozen=True)
class WsTargetMetrics:
    target: str
    evaluation_samples: int
    rmse: float
    baseline_rmse: float
    relative_skill: float
    interval_coverage: float
    correlation: float
    qualified: bool


@dataclass(frozen=True)
class WsChallengerReport:
    version: str
    symbol: str
    frames: int
    supervised_rows: int
    horizon_ms: int
    targets: tuple[WsTargetMetrics, ...]
    fast_execution_evidence_qualified: bool
    reasons: tuple[str, ...]
    research_only: bool = True
    paper_trading_authority: bool = False
    live_trading_authority: bool = False


def feature_vector(frame: NervousFeatureFrame) -> np.ndarray:
    return np.asarray(
        [
            frame.spread_bps,
            frame.microprice_offset_bps,
            frame.imbalance_l1,
            frame.imbalance_l5,
            frame.book_pressure_slope,
            frame.trade_imbalance_1s,
            frame.trade_imbalance_5s,
            np.log1p(max(0.0, frame.trade_intensity_1s)),
            np.log1p(max(0.0, frame.trade_intensity_5s)),
            np.tanh(frame.signed_trade_contracts_1s / 500.0),
            np.tanh(frame.signed_trade_contracts_5s / 2_000.0),
        ],
        dtype=float,
    )


def build_rows(
    frames: Sequence[NervousFeatureFrame],
    *,
    horizon_steps: int = 4,
    sample_interval_ms: int = 250,
) -> list[WsSupervisedRow]:
    ordered = sorted(frames, key=lambda frame: frame.timestamp_ms)
    if horizon_steps < 1:
        raise ValueError("horizon_steps must be positive")
    rows: list[WsSupervisedRow] = []
    for i in range(0, len(ordered) - horizon_steps):
        current = ordered[i]
        future = ordered[i + horizon_steps]
        rows.append(
            WsSupervisedRow(
                timestamp_ms=current.timestamp_ms,
                features=tuple(float(v) for v in feature_vector(current)),
                targets=(
                    float(future.trade_imbalance_1s),
                    float(future.spread_bps),
                    float(future.microprice_offset_bps),
                ),
                baselines=(
                    float(current.trade_imbalance_1s),
                    float(current.spread_bps),
                    float(current.microprice_offset_bps),
                ),
            )
        )
    return rows


class WsFastExecutionModel:
    def __init__(
        self,
        *,
        heads: int = 9,
        ridge: float = 1e-2,
        minimum_rows: int = 240,
        seed: int = 8210,
    ) -> None:
        if heads < 3:
            raise ValueError("heads must be >=3")
        if ridge <= 0:
            raise ValueError("ridge must be positive")
        self.heads = heads
        self.ridge = ridge
        self.minimum_rows = minimum_rows
        self.seed = seed
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None
        self._weights: list[np.ndarray] = []
        self._radii: np.ndarray | None = None

    @staticmethod
    def _design(x: np.ndarray) -> np.ndarray:
        return np.column_stack([np.ones(len(x)), x])

    def _standardize(self, x: np.ndarray) -> np.ndarray:
        if self._mean is None or self._std is None:
            raise RuntimeError("model not fitted")
        return (x - self._mean) / self._std

    def _predict_heads(self, x: np.ndarray) -> np.ndarray:
        z = self._standardize(x)
        design = self._design(z)
        return np.stack([design @ weights for weights in self._weights], axis=0)

    def fit_and_evaluate(
        self,
        rows: Sequence[WsSupervisedRow],
        *,
        symbol: str,
        frames: int,
        horizon_ms: int = 1_000,
    ) -> WsChallengerReport:
        ordered = sorted(rows, key=lambda row: row.timestamp_ms)
        if len(ordered) < self.minimum_rows:
            raise ValueError(
                f"insufficient websocket challenger rows: {len(ordered)} < {self.minimum_rows}"
            )
        x = np.asarray([row.features for row in ordered], dtype=float)
        y = np.asarray([row.targets for row in ordered], dtype=float)
        baseline = np.asarray([row.baselines for row in ordered], dtype=float)

        n = len(ordered)
        train_end = int(n * 0.70)
        cal_end = int(n * 0.85)
        if n - cal_end < 30:
            raise ValueError("websocket challenger evaluation slice too small")

        x_train, y_train = x[:train_end], y[:train_end]
        x_cal, y_cal = x[train_end:cal_end], y[train_end:cal_end]
        x_eval, y_eval = x[cal_end:], y[cal_end:]
        baseline_eval = baseline[cal_end:]

        self._mean = x_train.mean(axis=0)
        std = x_train.std(axis=0)
        self._std = np.where(std < 1e-9, 1.0, std)
        z_train = self._standardize(x_train)

        rng = np.random.default_rng(self.seed)
        eye = np.eye(z_train.shape[1] + 1)
        eye[0, 0] = 0.0
        self._weights = []
        for _ in range(self.heads):
            idx = rng.integers(0, len(z_train), size=len(z_train))
            design = self._design(z_train[idx])
            lhs = design.T @ design + self.ridge * eye
            rhs = design.T @ y_train[idx]
            self._weights.append(np.linalg.solve(lhs, rhs))

        cal_pred = self._predict_heads(x_cal).mean(axis=0)
        self._radii = np.quantile(
            np.abs(y_cal - cal_pred),
            0.90,
            axis=0,
            method="higher",
        )

        eval_heads = self._predict_heads(x_eval)
        pred = eval_heads.mean(axis=0)
        metrics: list[WsTargetMetrics] = []
        for j, target in enumerate(TARGET_NAMES):
            error = y_eval[:, j] - pred[:, j]
            baseline_error = y_eval[:, j] - baseline_eval[:, j]
            rmse = float(np.sqrt(np.mean(error**2)))
            baseline_rmse = float(np.sqrt(np.mean(baseline_error**2)))
            relative_skill = (
                1.0 - rmse / baseline_rmse if baseline_rmse > 1e-12 else 0.0
            )
            radius = float(self._radii[j])
            coverage = float(
                np.mean(
                    (y_eval[:, j] >= pred[:, j] - radius)
                    & (y_eval[:, j] <= pred[:, j] + radius)
                )
            )
            correlation = 0.0
            if (
                len(y_eval) >= 3
                and float(np.std(y_eval[:, j])) > 1e-12
                and float(np.std(pred[:, j])) > 1e-12
            ):
                correlation = float(np.corrcoef(y_eval[:, j], pred[:, j])[0, 1])
            qualified = bool(
                len(y_eval) >= 40
                and relative_skill >= 0.05
                and coverage >= 0.72
                and correlation >= 0.10
            )
            metrics.append(
                WsTargetMetrics(
                    target=target,
                    evaluation_samples=len(y_eval),
                    rmse=rmse,
                    baseline_rmse=baseline_rmse,
                    relative_skill=relative_skill,
                    interval_coverage=coverage,
                    correlation=correlation,
                    qualified=qualified,
                )
            )

        by_target = {metric.target: metric for metric in metrics}
        flow_ok = by_target["future_trade_imbalance_1s"].qualified
        microprice_ok = by_target["future_microprice_offset_bps"].qualified
        reasons: list[str] = []
        if not flow_ok:
            reasons.append("trade_flow_head_not_materially_better_than_persistence")
        if not microprice_ok:
            reasons.append("microprice_head_not_materially_better_than_persistence")
        return WsChallengerReport(
            version=CHRONOS_WS_CHALLENGER_VERSION,
            symbol=symbol,
            frames=frames,
            supervised_rows=len(ordered),
            horizon_ms=horizon_ms,
            targets=tuple(metrics),
            # Spread is a risk diagnostic, not a required forecast head. On tight
            # books its persistence baseline can be nearly perfect, making
            # "beat spread persistence" structurally meaningless.
            fast_execution_evidence_qualified=(flow_ok and microprice_ok),
            reasons=tuple(reasons),
        )


async def collect_frames(
    *,
    seconds: float = 90.0,
    sample_interval_seconds: float = 0.25,
) -> dict[str, list[NervousFeatureFrame]]:
    import websockets

    if seconds < 20:
        raise ValueError("capture window too short")
    accumulator = NervousAccumulator()
    frames = {symbol: [] for symbol in accumulator.symbols}
    args = []
    for symbol in accumulator.symbols:
        inst = accumulator.to_okx(symbol)
        args.extend(
            [
                {"channel": "books5", "instId": inst},
                {"channel": "trades", "instId": inst},
            ]
        )

    deadline = time.monotonic() + seconds
    next_sample = time.monotonic() + sample_interval_seconds
    async with websockets.connect(
        OKX_PUBLIC_WS,
        ping_interval=15,
        ping_timeout=15,
        max_size=4 * 1024 * 1024,
        open_timeout=8,
    ) as ws:
        await ws.send(
            json.dumps(
                {"id": "v821capture", "op": "subscribe", "args": args}
            )
        )
        while time.monotonic() < deadline:
            now = time.monotonic()
            timeout = min(0.10, max(0.01, next_sample - now))
            try:
                payload = await asyncio.wait_for(ws.recv(), timeout=timeout)
                accumulator.handle_message(json.loads(payload))
            except asyncio.TimeoutError:
                pass

            now = time.monotonic()
            if now >= next_sample:
                wall_ms = int(time.time() * 1_000)
                for symbol in accumulator.symbols:
                    if symbol not in accumulator.books:
                        continue
                    frames[symbol].append(
                        accumulator.frame(symbol, now_ms=wall_ms)
                    )
                next_sample += sample_interval_seconds

    return frames


async def run_live_challenger() -> dict:
    frames = await collect_frames()
    reports = []
    for symbol, symbol_frames in frames.items():
        rows = build_rows(symbol_frames, horizon_steps=4)
        report = WsFastExecutionModel().fit_and_evaluate(
            rows,
            symbol=symbol,
            frames=len(symbol_frames),
            horizon_ms=1_000,
        )
        reports.append(asdict(report))
    result = {
        "version": CHRONOS_WS_CHALLENGER_VERSION,
        "source": "OKX_PUBLIC_WS_BOOKS5_TRADES",
        "capture_seconds": 90,
        "sample_interval_ms": 250,
        "target_horizon_ms": 1_000,
        "reports": reports,
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }
    path = Path("artifacts/chronos-v821-ws-challenger.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True))
    return result


async def _main() -> None:
    print(json.dumps(await run_live_challenger(), indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
