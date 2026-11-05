from __future__ import annotations

import json
from typing import Any

from .chronos_match_ledger import audit_episodes
from .chronos_regime_gated_ab import run_regime_gated_ab
from .runtime_gateway import PaperRuntimeGateway, RuntimeGatewayFailure


async def run_chronos_runtime_cycle(
    settings: Any,
    *,
    vercel_oidc_token: str | None,
) -> dict:
    report = await run_regime_gated_ab()
    gateway = PaperRuntimeGateway(
        settings,
        vercel_oidc_token=vercel_oidc_token,
    )

    persistence: dict = {
        "attempted": False,
        "persisted": False,
        "duplicate": False,
        "error": None,
    }

    if report.get("ab") and str(report.get("verdict", "")).startswith("REGIME_MATCH_"):
        persistence["attempted"] = True
        try:
            stored = await gateway.chronos_record(report)
            persistence.update(
                {
                    "persisted": bool(stored.get("persisted", False)),
                    "duplicate": bool(stored.get("duplicate", False)),
                    "record": stored,
                }
            )
        except RuntimeGatewayFailure as exc:
            persistence["error"] = type(exc).__name__

    episodes: list[dict] = []
    history_error: str | None = None
    try:
        history = await gateway.chronos_list()
        raw_episodes = history.get("episodes") or []
        if isinstance(raw_episodes, list):
            episodes = [row for row in raw_episodes if isinstance(row, dict)]
    except RuntimeGatewayFailure as exc:
        history_error = type(exc).__name__

    # Preserve current matched evidence in the returned/logged ledger even when
    # the persistent gateway has not been upgraded yet.
    if report.get("ab") and str(report.get("verdict", "")).startswith("REGIME_MATCH_"):
        current_candle = int(report["gate_before"]["latest_closed_candle_start_ms"])
        if not any(
            int((row.get("gate_before") or {}).get("latest_closed_candle_start_ms", -1))
            == current_candle
            for row in episodes
        ):
            episodes.append(report)

    ledger = audit_episodes(episodes)
    result = {
        "status": "COMPLETED",
        "version": "v8.27-vercel-regime-monitor-0",
        "report": report,
        "ledger": ledger,
        "persistence": persistence,
        "history_error": history_error,
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
    }
    print(
        "CHRONOS_V826_MONITOR "
        + json.dumps(result, sort_keys=True, separators=(",", ":"))
    )
    return result
