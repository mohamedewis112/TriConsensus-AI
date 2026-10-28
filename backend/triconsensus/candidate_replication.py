from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CandidateReplicationResult:
    symbol: str
    setup: str
    regime: str
    side: str
    found: bool
    trades: int
    mean_net_return: float
    profit_factor: float | None
    folds: int
    profitable_folds: int
    bootstrap_lcb_95: float
    directional_replicated: bool
    full_evidence_gate_passed: bool
    reason: str


def _report_index(report: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in report.get("ranked") or []:
        setup = str(row.get("setup") or "")
        regime = str(row.get("regime") or "")
        side = str(row.get("side") or "")
        if setup and regime and side in {"LONG", "SHORT"}:
            indexed[(setup, regime, side)] = row
    return indexed


def evaluate_preregistered_candidates(
    *,
    manifest: dict[str, Any],
    reports_by_symbol: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rule = (
        manifest.get("interpretation", {})
        .get("directional_replication_rule", {})
    )
    min_trades = int(rule.get("minimum_replication_trades", 10))
    min_pf = float(rule.get("profit_factor_minimum", 1.1))
    min_folds = int(rule.get("minimum_fold_coverage", 3))
    profitable_fraction = float(
        rule.get("minimum_profitable_fold_fraction", 0.67)
    )

    indexes = {
        symbol: _report_index(report)
        for symbol, report in reports_by_symbol.items()
    }

    results: list[CandidateReplicationResult] = []
    for candidate in manifest.get("candidates") or []:
        symbol = str(candidate["symbol"])
        setup = str(candidate["setup"])
        regime = str(candidate["regime"])
        side = str(candidate["side"])
        row = indexes.get(symbol, {}).get((setup, regime, side))

        if row is None:
            results.append(
                CandidateReplicationResult(
                    symbol=symbol,
                    setup=setup,
                    regime=regime,
                    side=side,
                    found=False,
                    trades=0,
                    mean_net_return=0.0,
                    profit_factor=None,
                    folds=0,
                    profitable_folds=0,
                    bootstrap_lcb_95=0.0,
                    directional_replicated=False,
                    full_evidence_gate_passed=False,
                    reason="candidate_absent_in_replication",
                )
            )
            continue

        trades = int(row.get("trades") or 0)
        mean_net = float(row.get("mean_net_return") or 0.0)
        pf_value = row.get("profit_factor")
        pf = float(pf_value) if pf_value is not None else None
        folds = int(row.get("folds") or 0)
        profitable_folds = int(row.get("profitable_folds") or 0)
        lcb = float(row.get("bootstrap_lcb_95") or 0.0)

        required_profitable = max(
            1,
            int((folds * profitable_fraction) + 0.999999),
        )
        directional = (
            trades >= min_trades
            and mean_net > 0
            and pf is not None
            and pf >= min_pf
            and folds >= min_folds
            and profitable_folds >= required_profitable
        )

        full_gate = bool(row.get("qualified"))
        if full_gate:
            reason = "full_evidence_gate_passed"
        elif directional:
            reason = "directionally_replicated_research_only"
        else:
            reason = "replication_failed"

        results.append(
            CandidateReplicationResult(
                symbol=symbol,
                setup=setup,
                regime=regime,
                side=side,
                found=True,
                trades=trades,
                mean_net_return=mean_net,
                profit_factor=pf,
                folds=folds,
                profitable_folds=profitable_folds,
                bootstrap_lcb_95=lcb,
                directional_replicated=directional,
                full_evidence_gate_passed=full_gate,
                reason=reason,
            )
        )

    serialized = [asdict(row) for row in results]
    return {
        "schema_version": 1,
        "experiment": manifest.get("experiment"),
        "setup_version": manifest.get("setup_version"),
        "replication_window": manifest.get("replication_window"),
        "default_action": "NO_TRADE",
        "candidate_count": len(serialized),
        "directionally_replicated_count": sum(
            1 for row in serialized if row["directional_replicated"]
        ),
        "full_evidence_pass_count": sum(
            1 for row in serialized if row["full_evidence_gate_passed"]
        ),
        "results": serialized,
    }
