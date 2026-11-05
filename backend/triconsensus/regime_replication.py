from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ReplicatedRegime:
    regime: str
    side: str
    discovery_trades: int
    replication_trades: int
    discovery_mean_net_return: float
    replication_mean_net_return: float
    discovery_profit_factor: float | None
    replication_profit_factor: float | None
    worst_mean_net_return: float
    replicated: bool
    reason: str


def _index(rows: Iterable[dict]) -> dict[tuple[str, str], dict]:
    indexed: dict[tuple[str, str], dict] = {}
    for row in rows:
        regime = str(row.get("regime") or "")
        side = str(row.get("side") or "")
        if not regime or side not in {"LONG", "SHORT"}:
            continue
        indexed[(regime, side)] = row
    return indexed


def compare_regime_reports(
    discovery: dict,
    replication: dict,
    *,
    minimum_trades_each: int = 40,
    minimum_mean_net_return_each: float = 0.0003,
) -> dict:
    if discovery.get("symbol") != replication.get("symbol"):
        raise ValueError("discovery and replication symbols must match")
    if discovery.get("timeframe") != replication.get("timeframe"):
        raise ValueError("discovery and replication timeframes must match")

    first = _index(discovery.get("qualified") or [])
    second_all = _index(replication.get("ranked") or [])

    rows: list[ReplicatedRegime] = []
    for key, drow in sorted(first.items()):
        rrow = second_all.get(key)
        if rrow is None:
            rows.append(
                ReplicatedRegime(
                    regime=key[0],
                    side=key[1],
                    discovery_trades=int(drow.get("trades") or 0),
                    replication_trades=0,
                    discovery_mean_net_return=float(drow.get("mean_net_return") or 0),
                    replication_mean_net_return=0.0,
                    discovery_profit_factor=drow.get("profit_factor"),
                    replication_profit_factor=None,
                    worst_mean_net_return=0.0,
                    replicated=False,
                    reason="missing_in_replication",
                )
            )
            continue

        discovery_mean = float(drow.get("mean_net_return") or 0)
        replication_mean = float(rrow.get("mean_net_return") or 0)
        discovery_trades = int(drow.get("trades") or 0)
        replication_trades = int(rrow.get("trades") or 0)

        passed = (
            bool(drow.get("qualified"))
            and bool(rrow.get("qualified"))
            and discovery_trades >= minimum_trades_each
            and replication_trades >= minimum_trades_each
            and discovery_mean >= minimum_mean_net_return_each
            and replication_mean >= minimum_mean_net_return_each
            and float(drow.get("bootstrap_lcb_95") or 0) > 0
            and float(rrow.get("bootstrap_lcb_95") or 0) > 0
        )

        rows.append(
            ReplicatedRegime(
                regime=key[0],
                side=key[1],
                discovery_trades=discovery_trades,
                replication_trades=replication_trades,
                discovery_mean_net_return=discovery_mean,
                replication_mean_net_return=replication_mean,
                discovery_profit_factor=drow.get("profit_factor"),
                replication_profit_factor=rrow.get("profit_factor"),
                worst_mean_net_return=min(discovery_mean, replication_mean),
                replicated=passed,
                reason="replicated" if passed else "replication_gate_failed",
            )
        )

    approved = [row for row in rows if row.replicated]
    return {
        "schema_version": 1,
        "symbol": discovery.get("symbol"),
        "timeframe": discovery.get("timeframe"),
        "default_action": "NO_TRADE",
        "discovery_window_end": discovery.get("archive_end_day"),
        "replication_window_end": replication.get("archive_end_day"),
        "candidate_count": len(rows),
        "replicated_count": len(approved),
        "replicated_pairs": [
            {
                "regime": row.regime,
                "side": row.side,
                "worst_mean_net_return": row.worst_mean_net_return,
                "discovery_trades": row.discovery_trades,
                "replication_trades": row.replication_trades,
            }
            for row in approved
        ],
        "audit": [row.__dict__ for row in rows],
    }
