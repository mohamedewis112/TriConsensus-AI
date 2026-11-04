from __future__ import annotations

from triconsensus.chronos_match_ledger import audit_episodes


def _episode(candle: int, *, improvements=(0.001, 0.0012, 0.0014), winner=True):
    horizons = []
    for horizon, improvement in zip((5, 15, 30), improvements, strict=True):
        horizons.append({
            "horizon_seconds": horizon,
            "improvement_per_opportunity": improvement,
            "baseline_mean_net": -0.001,
            "filtered_mean_net_per_opportunity": -0.001 + improvement,
            "baseline_max_drawdown": 0.03,
            "filtered_max_drawdown": 0.01,
            "vetoed_losses_fraction": 0.80 if winner else 0.30,
            "vetoed_winners_fraction": 0.20 if winner else 0.70,
        })
    return {
        "generated_at": f"2026-11-04T{candle % 24:02d}:00:00+00:00",
        "research_only": True,
        "gate_before": {
            "matched": True,
            "latest_closed_candle_start_ms": candle,
        },
        "gate_after": {"matched": True},
        "verdict": "REGIME_MATCH_TIMING_HELPED_THIS_EPISODE",
        "ab": {"horizons": horizons},
    }


def test_ledger_requires_eight_independent_candles():
    report = audit_episodes([_episode(i) for i in range(7)])
    assert report["episode_count"] == 7
    assert report["evidence_qualified"] is False
    assert "insufficient_independent_regime_match_episodes" in report["reasons"]


def test_ledger_deduplicates_same_closed_candle_and_can_qualify():
    reports = [_episode(i) for i in range(8)]
    reports.append(_episode(7))
    report = audit_episodes(reports)
    assert report["episode_count"] == 8
    assert report["evidence_qualified"] is True
    assert sum(row["qualified"] for row in report["horizons"]) == 3


def test_ledger_rejects_when_primary_horizon_is_not_repeatable():
    reports = [
        _episode(i, improvements=(0.001, -0.001, 0.001))
        for i in range(8)
    ]
    report = audit_episodes(reports)
    assert report["evidence_qualified"] is False
    primary = next(row for row in report["horizons"] if row["horizon_seconds"] == 15)
    assert primary["qualified"] is False
