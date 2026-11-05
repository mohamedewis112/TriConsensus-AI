from __future__ import annotations

from triconsensus.chronos_ws_challenger import (
    WsChallengerReport,
    WsTargetMetrics,
)
from triconsensus.chronos_ws_stability import audit_windows


def _report(symbol: str, *, flow_skill: float, micro_skill: float, good: bool = True):
    def metric(target, skill, corr=0.2, coverage=0.9):
        return WsTargetMetrics(
            target=target,
            evaluation_samples=50,
            rmse=0.8,
            baseline_rmse=1.0,
            relative_skill=skill,
            interval_coverage=coverage,
            correlation=corr,
            qualified=(skill >= 0.05 and corr >= 0.10 and coverage >= 0.72),
        )
    return WsChallengerReport(
        version="test",
        symbol=symbol,
        frames=280,
        supervised_rows=276,
        horizon_ms=1000,
        targets=(
            metric("future_trade_imbalance_1s", flow_skill),
            metric("future_spread_bps", 0.0, corr=0.0),
            metric("future_microprice_offset_bps", micro_skill),
        ),
        fast_execution_evidence_qualified=good,
        reasons=(),
    )


def test_stability_requires_repetition_across_windows():
    reports = [
        _report("ETHUSDT", flow_skill=0.20, micro_skill=0.16),
        _report("ETHUSDT", flow_skill=0.18, micro_skill=0.14),
        _report("ETHUSDT", flow_skill=0.12, micro_skill=0.11),
        _report("ETHUSDT", flow_skill=-0.02, micro_skill=-0.01, good=False),
    ]
    audit = audit_windows(reports)
    assert audit.fast_execution_stable is True
    by_target = {row.target: row for row in audit.targets}
    assert by_target["future_trade_imbalance_1s"].qualified_windows == 3
    assert by_target["future_microprice_offset_bps"].qualified_windows == 3
    assert audit.paper_trading_authority is False
    assert audit.live_trading_authority is False


def test_single_good_window_cannot_qualify_stability():
    reports = [
        _report("BTCUSDT", flow_skill=0.25, micro_skill=0.20),
        _report("BTCUSDT", flow_skill=-0.05, micro_skill=-0.04, good=False),
        _report("BTCUSDT", flow_skill=0.01, micro_skill=0.00, good=False),
        _report("BTCUSDT", flow_skill=-0.02, micro_skill=0.03, good=False),
    ]
    audit = audit_windows(reports)
    assert audit.fast_execution_stable is False
    assert audit.reasons
