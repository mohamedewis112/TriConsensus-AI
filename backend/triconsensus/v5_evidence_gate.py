from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class EvidenceCandidate:
    feature: str
    horizon: int
    sign: str
    worst_abs_rank_ic: float
    worst_cost_adjusted_spread: float
    symbols: tuple[str, ...]
    windows: int
    replicated: bool


def _qualified_map(report: dict) -> dict[tuple[str, int], dict]:
    rows = report.get("pure_qualified") or []
    result: dict[tuple[str, int], dict] = {}
    for row in rows:
        feature = str(row.get("feature") or "")
        horizon = int(row.get("horizon") or 0)
        if not feature or horizon <= 0:
            continue
        result[(feature, horizon)] = row
    return result


def _sign(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"


def replicate_symbol(
    discovery: dict,
    replication: dict,
) -> list[EvidenceCandidate]:
    if discovery.get("symbol") != replication.get("symbol"):
        raise ValueError("symbol mismatch")
    if discovery.get("timeframe") != replication.get("timeframe"):
        raise ValueError("timeframe mismatch")

    first = _qualified_map(discovery)
    second = _qualified_map(replication)
    symbol = str(discovery["symbol"])
    candidates: list[EvidenceCandidate] = []

    for key, left in sorted(first.items()):
        right = second.get(key)
        if right is None:
            continue

        left_ic = float(left["mean_rank_ic"])
        right_ic = float(right["mean_rank_ic"])
        sign = _sign(left_ic)
        if sign == "zero" or sign != _sign(right_ic):
            continue

        left_spread = float(left["mean_cost_adjusted_decile_spread"])
        right_spread = float(right["mean_cost_adjusted_decile_spread"])
        candidates.append(
            EvidenceCandidate(
                feature=key[0],
                horizon=key[1],
                sign=sign,
                worst_abs_rank_ic=min(abs(left_ic), abs(right_ic)),
                worst_cost_adjusted_spread=min(left_spread, right_spread),
                symbols=(symbol,),
                windows=2,
                replicated=True,
            )
        )

    return sorted(
        candidates,
        key=lambda row: (
            row.worst_abs_rank_ic,
            row.worst_cost_adjusted_spread,
        ),
        reverse=True,
    )


def cross_symbol_replication(
    reports: dict[str, tuple[dict, dict]],
) -> dict:
    if len(reports) < 2:
        raise ValueError("cross-symbol replication requires at least two symbols")

    per_symbol = {
        symbol: replicate_symbol(discovery, replication)
        for symbol, (discovery, replication) in reports.items()
    }
    indexed = {
        symbol: {
            (row.feature, row.horizon): row
            for row in rows
        }
        for symbol, rows in per_symbol.items()
    }

    common_keys = set.intersection(
        *(set(rows) for rows in indexed.values())
    )
    approved: list[EvidenceCandidate] = []

    for key in sorted(common_keys):
        rows = [indexed[symbol][key] for symbol in sorted(indexed)]
        signs = {row.sign for row in rows}
        if len(signs) != 1:
            continue
        approved.append(
            EvidenceCandidate(
                feature=key[0],
                horizon=key[1],
                sign=rows[0].sign,
                worst_abs_rank_ic=min(row.worst_abs_rank_ic for row in rows),
                worst_cost_adjusted_spread=min(
                    row.worst_cost_adjusted_spread for row in rows
                ),
                symbols=tuple(sorted(indexed)),
                windows=sum(row.windows for row in rows),
                replicated=True,
            )
        )

    approved.sort(
        key=lambda row: (
            row.worst_abs_rank_ic,
            row.worst_cost_adjusted_spread,
        ),
        reverse=True,
    )
    return {
        "schema_version": 1,
        "default_action": "NO_TRADE",
        "required_context_lag_minutes": 15,
        "symbol_replication": {
            symbol: [row.__dict__ for row in rows]
            for symbol, rows in per_symbol.items()
        },
        "cross_symbol_count": len(approved),
        "cross_symbol_replicated": [row.__dict__ for row in approved],
    }
