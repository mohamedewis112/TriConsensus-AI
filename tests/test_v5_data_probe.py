from triconsensus.derivatives_data import (
    AccountRatioPoint,
    FundingPoint,
    OpenInterestPoint,
)
from triconsensus.v5_data_probe import build_probe_report, expected_grid


def _grid(start=1_000_000, count=96):
    return [start + i * 900_000 for i in range(count)]


def test_expected_grid_is_15m_inclusive():
    grid = expected_grid(start_ms=1_000_000, end_ms=2_800_000)
    assert grid == [1_000_000, 1_900_000, 2_800_000]


def test_probe_passes_complete_derivatives_history():
    grid = _grid(count=96)
    funding = [
        FundingPoint("BTCUSDT", grid[0] - 900_000, 0.0001),
        FundingPoint("BTCUSDT", grid[0], 0.0001),
        FundingPoint("BTCUSDT", grid[32], 0.0002),
        FundingPoint("BTCUSDT", grid[64], -0.0001),
    ]
    # Probe requires >=10 funding points in-window. Use 8h-equivalent cadence
    # over a longer synthetic window while keeping 15m coverage complete.
    long_grid = _grid(count=384)
    funding = [
        FundingPoint("BTCUSDT", long_grid[0] - 900_000, 0.0001)
    ] + [
        FundingPoint("BTCUSDT", long_grid[i], 0.0001)
        for i in range(0, len(long_grid), 32)
    ]
    oi = [
        OpenInterestPoint("BTCUSDT", ts, 1000.0 + i)
        for i, ts in enumerate(long_grid)
    ]
    ratios = [
        AccountRatioPoint("BTCUSDT", ts, 0.52, 0.48, 0.52 / 0.48)
        for ts in long_grid
    ]
    report = build_probe_report(
        symbol="BTCUSDT",
        start_ms=long_grid[0],
        end_ms=long_grid[-1],
        funding=funding,
        open_interest=oi,
        account_ratio=ratios,
    )
    assert report["quality_gate_passed"] is True
    assert report["quality"]["oi_coverage_ratio"] == 1.0
    assert report["quality"]["account_ratio_coverage_ratio"] == 1.0
    assert report["quality"]["aligned_complete_coverage_ratio"] == 1.0


def test_probe_fails_closed_on_sparse_open_interest():
    grid = _grid(count=384)
    funding = [
        FundingPoint("ETHUSDT", grid[0] - 900_000, 0.0001)
    ] + [
        FundingPoint("ETHUSDT", grid[i], 0.0001)
        for i in range(0, len(grid), 32)
    ]
    oi = [
        OpenInterestPoint("ETHUSDT", ts, 1000.0)
        for ts in grid[::2]
    ]
    ratios = [
        AccountRatioPoint("ETHUSDT", ts, 0.5, 0.5, 1.0)
        for ts in grid
    ]
    report = build_probe_report(
        symbol="ETHUSDT",
        start_ms=grid[0],
        end_ms=grid[-1],
        funding=funding,
        open_interest=oi,
        account_ratio=ratios,
    )
    assert report["quality_gate_passed"] is False
    assert report["quality"]["oi_coverage_ratio"] < 0.95
