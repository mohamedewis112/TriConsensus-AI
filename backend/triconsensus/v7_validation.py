from __future__ import annotations

from dataclasses import dataclass
import bisect


@dataclass(frozen=True)
class PurgedTimeSplit:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def purged_walk_forward_splits(
    timestamps_ms: list[int],
    *,
    train_size: int,
    test_size: int,
    embargo_ms: int,
    step_size: int | None = None,
) -> list[PurgedTimeSplit]:
    if not timestamps_ms:
        raise ValueError("timestamps cannot be empty")
    if any(b < a for a, b in zip(timestamps_ms, timestamps_ms[1:])):
        raise ValueError("timestamps must be sorted")
    if train_size <= 0 or test_size <= 0 or embargo_ms < 0:
        raise ValueError("invalid split parameters")

    step = step_size or test_size
    if step <= 0:
        raise ValueError("step_size must be positive")

    splits: list[PurgedTimeSplit] = []
    train_start = 0
    n = len(timestamps_ms)
    while True:
        train_end = train_start + train_size
        if train_end >= n:
            break
        embargo_cutoff = timestamps_ms[train_end - 1] + embargo_ms
        test_start = bisect.bisect_right(timestamps_ms, embargo_cutoff, lo=train_end)
        test_end = test_start + test_size
        if test_end > n:
            break
        splits.append(
            PurgedTimeSplit(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        train_start += step
    return splits


def purged_train_end_before_holdout(
    timestamps_ms: list[int],
    *,
    holdout_start: int,
    embargo_ms: int,
) -> int:
    if not 0 < holdout_start < len(timestamps_ms):
        raise ValueError("invalid holdout start")
    cutoff = timestamps_ms[holdout_start] - embargo_ms
    return bisect.bisect_left(timestamps_ms, cutoff, hi=holdout_start)
