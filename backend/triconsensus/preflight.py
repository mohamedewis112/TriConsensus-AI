from __future__ import annotations

import httpx

from .config import Settings
from .db import PersistenceFailure, SupabaseRepository
from .market_data import BybitPublicClient, MarketDataFailure


async def run_preflight(settings: Settings) -> dict:
    checks: dict[str, dict] = {}

    try:
        ticker = await BybitPublicClient(settings).ticker("BTCUSDT")
        checks["bybit"] = {
            "ok": True,
            "symbol": ticker["symbol"],
            "market_data_age_ms": max(
                0,
                int(ticker["received_ms"]) - int(ticker["server_time_ms"]),
            ),
        }
    except (MarketDataFailure, ValueError) as exc:
        checks["bybit"] = {"ok": False, "reason": type(exc).__name__}

    try:
        repo = SupabaseRepository(settings)
        experiment = repo.get_experiment("V1")
        checks["supabase"] = {
            "ok": True,
            "project_configured": True,
            "v1_experiment_exists": experiment is not None,
        }
    except PersistenceFailure as exc:
        checks["supabase"] = {"ok": False, "reason": type(exc).__name__}

    if settings.openrouter_api_key is None:
        checks["openrouter"] = {"ok": False, "reason": "OPENROUTER_API_KEY_missing"}
    else:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.openrouter_base_url.rstrip('/')}/models",
                    headers={
                        "Authorization": (
                            "Bearer "
                            + settings.openrouter_api_key.get_secret_value()
                        )
                    },
                )
                response.raise_for_status()
                body = response.json()
                model_ids = {
                    str(item.get("id"))
                    for item in (body.get("data") or [])
                    if item.get("id")
                }
            checks["openrouter"] = {
                "ok": settings.model_id in model_ids,
                "model_id": settings.model_id,
                "reason": (
                    None
                    if settings.model_id in model_ids
                    else "configured_model_not_available"
                ),
            }
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            checks["openrouter"] = {"ok": False, "reason": type(exc).__name__}

    checks["safety"] = {
        "ok": str(settings.trading_mode).lower() in {"paper", "dry_run", "demo"},
        "live_trading": False,
        "mode": str(settings.trading_mode),
    }

    ready = all(check.get("ok") is True for check in checks.values())
    return {
        "ready_for_paper_cycle": ready,
        "ready_for_live_trading": False,
        "checks": checks,
    }
