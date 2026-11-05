from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WalkForwardSplit:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def walk_forward_splits(
    n_samples: int,
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
    embargo_size: int = 0,
) -> list[WalkForwardSplit]:
    if n_samples <= 0 or train_size <= 0 or test_size <= 0:
        raise ValueError("sample and window sizes must be positive")
    if embargo_size < 0:
        raise ValueError("embargo_size cannot be negative")
    step = step_size or test_size
    if step <= 0:
        raise ValueError("step_size must be positive")

    splits: list[WalkForwardSplit] = []
    train_start = 0
    while True:
        train_end = train_start + train_size
        test_start = train_end + embargo_size
        test_end = test_start + test_size
        if test_end > n_samples:
            break
        splits.append(
            WalkForwardSplit(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        train_start += step
    return splits


def forward_return_targets(closes: list[float], *, horizon: int) -> list[float | None]:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    out: list[float | None] = [None] * len(closes)
    for i in range(0, len(closes) - horizon):
        current = closes[i]
        future = closes[i + horizon]
        if current <= 0:
            raise ValueError("prices must be positive")
        out[i] = (future / current) - 1.0
    return out
