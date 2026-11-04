from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from statistics import median
from typing import Iterable


CHRONOS_MATCH_LEDGER_VERSION = "v8.27-match-ledger-0"
PRIMARY_HORIZON_SECONDS = 15
MIN_EPISODES = 8
MIN_POSITIVE_EPISODE_FRACTION = 0.625
HORIZONS = (5, 15, 30)


@dataclass(frozen=True)
class HorizonEvidence:
    horizon_seconds: int
    episodes: int
    positive_improvement_episodes: int
    positive_improvement_fraction: float
    median_improvement_per_opportunity: float
    median_baseline_mean_net: float
    median_filtered_mean_net: float
    median_baseline_max_drawdown: float
    median_filtered_max_drawdown: float
    median_vetoed_losses_fraction: float
    median_vetoed_winners_fraction: float
    qualified: bool


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _valid_episode(report: dict) -> bool:
    if not isinstance(report, dict):
        return False
    if report.get("research_only") is not True:
        return False
    gate_before = report.get("gate_before") or {}
    gate_after = report.get("gate_after") or {}
    if gate_before.get("matched") is not True:
        return False
    if gate_after and gate_after.get("matched") is not True:
        return False
    if not report.get("ab"):
        return False
    verdict = str(report.get("verdict") or "")
    return verdict.startswith("REGIME_MATCH_")


def _episode_key(report: dict) -> int:
    gate = report.get("gate_before") or {}
    return int(gate["latest_closed_candle_start_ms"])


def dedupe_valid_episodes(reports: Iterable[dict]) -> list[dict]:
    by_candle: dict[int, dict] = {}
    for report in reports:
        if not _valid_episode(report):
            continue
        key = _episode_key(report)
        current = by_candle.get(key)
        if current is None:
            by_candle[key] = report
            continue
        # Preserve the later generated report deterministically if duplicate
        # monitoring runs observed the same closed 15m candle.
        if str(report.get("generated_at", "")) > str(current.get("generated_at", "")):
            by_candle[key] = report
    return [by_candle[key] for key in sorted(by_candle)]


def _horizon_row(report: dict, horizon: int) -> dict:
    rows = (report.get("ab") or {}).get("horizons") or []
    return next(row for row in rows if int(row["horizon_seconds"]) == horizon)


def audit_episodes(reports: Iterable[dict]) -> dict:
    episodes = dedupe_valid_episodes(reports)
    horizon_evidence: list[HorizonEvidence] = []

    for horizon in HORIZONS:
        rows = [_horizon_row(report, horizon) for report in episodes]
        improvements = [float(row["improvement_per_opportunity"]) for row in rows]
        baseline_net = [float(row["baseline_mean_net"]) for row in rows]
        filtered_net = [float(row["filtered_mean_net_per_opportunity"]) for row in rows]
        baseline_dd = [float(row["baseline_max_drawdown"]) for row in rows]
        filtered_dd = [float(row["filtered_max_drawdown"]) for row in rows]
        veto_losses = [float(row["vetoed_losses_fraction"]) for row in rows]
        veto_winners = [float(row["vetoed_winners_fraction"]) for row in rows]

        count = len(rows)
        positive = sum(value > 0 for value in improvements)
        fraction = positive / count if count else 0.0
        med_improvement = float(median(improvements)) if improvements else 0.0
        med_base_net = float(median(baseline_net)) if baseline_net else 0.0
        med_filtered_net = float(median(filtered_net)) if filtered_net else 0.0
        med_base_dd = float(median(baseline_dd)) if baseline_dd else 0.0
        med_filtered_dd = float(median(filtered_dd)) if filtered_dd else 0.0
        med_veto_losses = float(median(veto_losses)) if veto_losses else 0.0
        med_veto_winners = float(median(veto_winners)) if veto_winners else 0.0

        qualified = bool(
            count >= MIN_EPISODES
            and fraction >= MIN_POSITIVE_EPISODE_FRACTION
            and med_improvement > 0.0
            and med_filtered_dd < med_base_dd
            and med_veto_losses > med_veto_winners
        )
        horizon_evidence.append(
            HorizonEvidence(
                horizon_seconds=horizon,
                episodes=count,
                positive_improvement_episodes=positive,
                positive_improvement_fraction=fraction,
                median_improvement_per_opportunity=med_improvement,
                median_baseline_mean_net=med_base_net,
                median_filtered_mean_net=med_filtered_net,
                median_baseline_max_drawdown=med_base_dd,
                median_filtered_max_drawdown=med_filtered_dd,
                median_vetoed_losses_fraction=med_veto_losses,
                median_vetoed_winners_fraction=med_veto_winners,
                qualified=qualified,
            )
        )

    by_horizon = {row.horizon_seconds: row for row in horizon_evidence}
    qualified_horizons = sum(row.qualified for row in horizon_evidence)
    evidence_qualified = bool(
        by_horizon[PRIMARY_HORIZON_SECONDS].qualified
        and qualified_horizons >= 2
    )
    reasons: list[str] = []
    if len(episodes) < MIN_EPISODES:
        reasons.append("insufficient_independent_regime_match_episodes")
    if not by_horizon[PRIMARY_HORIZON_SECONDS].qualified:
        reasons.append("primary_15s_horizon_not_qualified")
    if qualified_horizons < 2:
        reasons.append("fewer_than_two_horizons_qualified")

    return {
        "version": CHRONOS_MATCH_LEDGER_VERSION,
        "episode_count": len(episodes),
        "episode_candle_start_ms": [_episode_key(report) for report in episodes],
        "primary_horizon_seconds": PRIMARY_HORIZON_SECONDS,
        "minimum_episodes": MIN_EPISODES,
        "minimum_positive_episode_fraction": MIN_POSITIVE_EPISODE_FRACTION,
        "horizons": [asdict(row) for row in horizon_evidence],
        "evidence_qualified": evidence_qualified,
        "reasons": reasons,
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }


def audit_directory(directory: Path) -> dict:
    reports = []
    for path in sorted(directory.rglob("*.json")):
        report = _load_json(path)
        if report is not None:
            reports.append(report)
    return audit_episodes(reports)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="artifacts/v826-history")
    parser.add_argument("--output", default="artifacts/chronos-v827-match-ledger.json")
    args = parser.parse_args()

    result = audit_directory(Path(args.input_dir))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
