from __future__ import annotations

from datetime import UTC, datetime
import math

import httpx
import pandas as pd

from .account_state import build_account_state
from .agents import AgentService
from .binance_live import BinanceGlobalRatioClient, BinanceLiveContextFailure
from .config import Settings
from .features import build_features
from .levels import build_trade_levels
from .llm import LLMFailure, build_llm_provider
from .market_data import BybitPublicClient, MarketDataFailure
from .orchestrator import DecisionOrchestrator
from .risk import DeterministicRiskEngine
from .v51_forward import (
    CONTEXT_LAG_MINUTES,
    FEATURE,
    HORIZON_MINUTES,
    RELATIONSHIP,
    ROUND_TRIP_RESEARCH_COST,
    STRATEGY_VERSION,
    V51_MODEL_FAMILY,
)
from .v6_agents import DynamicVolatilityRiskAgent, MetaRLPortfolioAdapterAgent
from .v6_models import VolatilityRiskState
from .v6_reward import build_portfolio_state


FROZEN_THRESHOLDS = {
    "BTCUSDT": {
        "low": -0.019545157125130854,
        "high": 0.020643022165240422,
        "calibration_hash": "84c9370ad5c25383cef7b7c00be15b24d0c5f6f636742e925a78779b47a017ef",
    },
    "ETHUSDT": {
        "low": -0.01949802950955428,
        "high": 0.020743615937876794,
        "calibration_hash": "84c9370ad5c25383cef7b7c00be15b24d0c5f6f636742e925a78779b47a017ef",
    },
}


def _frame(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candles)[["open", "high", "low", "close", "volume"]]


async def _binance_orderbook_imbalance(symbol: str) -> float:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        response = await client.get(
            "https://fapi.binance.com/fapi/v1/depth",
            params={"symbol": symbol, "limit": 20},
        )
        response.raise_for_status()
        payload = response.json()

    bids = [(float(price), float(size)) for price, size in payload.get("bids", [])]
    asks = [(float(price), float(size)) for price, size in payload.get("asks", [])]
    if not bids or not asks:
        raise ValueError("empty Binance orderbook")
    bid_depth = sum(size for _, size in bids)
    ask_depth = sum(size for _, size in asks)
    total = bid_depth + ask_depth
    return (bid_depth - ask_depth) / total if total > 0 else 0.0


async def run_live_research_cycle(
    settings: Settings,
    *,
    symbol: str,
    vercel_oidc_token: str | None,
) -> dict:
    normalized = symbol.upper().replace("-", "").replace("/", "")
    if normalized not in FROZEN_THRESHOLDS:
        raise ValueError("research cycle supports BTCUSDT and ETHUSDT only")
    if settings.v6_marl_enforcement_active:
        raise ValueError("research cycle refuses V6 enforcement")

    display_symbol = "BTC/USDT" if normalized == "BTCUSDT" else "ETH/USDT"
    threshold = FROZEN_THRESHOLDS[normalized]
    strategy_client = BinanceGlobalRatioClient(
        context_lag_minutes=CONTEXT_LAG_MINUTES,
        max_context_age_minutes=45,
    )

    try:
        live_signal = await strategy_client.signal(
            normalized,
            low_threshold=float(threshold["low"]),
            high_threshold=float(threshold["high"]),
        )
    except (BinanceLiveContextFailure, ValueError) as exc:
        return {
            "symbol": display_symbol,
            "outcome": "NO_TRADE",
            "reason": "v51_live_signal_unavailable",
            "error": type(exc).__name__,
            "detail": str(exc)[:200],
            "paper_only": True,
            "persisted": False,
            "execution_performed": False,
        }

    signal_payload = {
        "strategy_version": STRATEGY_VERSION,
        "model_family": V51_MODEL_FAMILY,
        "feature_name": FEATURE,
        "feature_value": live_signal.feature_value,
        "relationship": RELATIONSHIP,
        "horizon_minutes": HORIZON_MINUTES,
        "context_lag_minutes": CONTEXT_LAG_MINUTES,
        "round_trip_research_cost": ROUND_TRIP_RESEARCH_COST,
        "context_timestamp_ms": live_signal.context_timestamp_ms,
        "context_age_ms": live_signal.context_age_ms,
        "low_threshold": live_signal.low_threshold,
        "high_threshold": live_signal.high_threshold,
        "calibration_config_hash": threshold["calibration_hash"],
        "signal": live_signal.signal,
        "reason": live_signal.reason,
        "default_action": "NO_TRADE",
        "frozen_protocol": True,
    }

    if live_signal.signal == "NO_TRADE":
        return {
            "symbol": display_symbol,
            "v51_signal": signal_payload,
            "outcome": "NO_TRADE",
            "reason": "frozen_v51_inside_calibrated_deciles",
            "agents_invoked": False,
            "paper_only": True,
            "persisted": False,
            "execution_performed": False,
        }

    market = BybitPublicClient(settings)
    try:
        snapshot = await market.multi_timeframe_snapshot(normalized)
        iv_snapshot = await market.implied_volatility(normalized)
        binance_imbalance = await _binance_orderbook_imbalance(normalized)
    except (MarketDataFailure, httpx.HTTPError, ValueError, KeyError) as exc:
        return {
            "symbol": display_symbol,
            "v51_signal": signal_payload,
            "outcome": "NO_TRADE",
            "reason": "market_evidence_unavailable",
            "error": type(exc).__name__,
            "paper_only": True,
            "persisted": False,
            "execution_performed": False,
        }

    timeframe_features: dict[str, dict] = {}
    for timeframe, candles in snapshot["candles"].items():
        timeframe_features[timeframe] = build_features(
            _frame(candles),
            bid=snapshot["ticker"]["bid"],
            ask=snapshot["ticker"]["ask"],
            orderbook=snapshot["orderbook"],
        )
    primary = timeframe_features["15m"]
    allowed_levels = build_trade_levels(
        last_price=float(snapshot["ticker"]["last_price"]),
        atr=float(primary["atr_14"]),
        min_risk_reward=settings.min_risk_reward,
    )

    observed_at = datetime.now(UTC)
    account_state = build_account_state([], settings, now=observed_at)
    portfolio_state = build_portfolio_state(
        [],
        window=settings.v6_reward_window,
        minimum_warmup_trades_per_asset=settings.v6_min_warmup_trades_per_asset,
        as_of=observed_at,
    )
    realized_annualized = float(primary["realized_vol_20"]) * math.sqrt(
        (365 * 24 * 4) / 20
    )
    volatility_state = VolatilityRiskState(
        symbol=display_symbol,
        side=live_signal.signal,
        entry_price=float(snapshot["ticker"]["last_price"]),
        atr_14=float(primary["atr_14"]),
        realized_volatility=max(0.0, realized_annualized),
        implied_volatility=float(iv_snapshot["mark_iv"]),
        bybit_orderbook_imbalance=float(snapshot["orderbook"]["imbalance"]),
        binance_orderbook_imbalance=float(binance_imbalance),
        spread_bps=float(primary.get("spread_bps") or 0),
        market_regime=(
            f"{primary.get('trend', 'unknown')}|"
            f"adx={float(primary.get('adx_14') or 0):.2f}"
        ),
        microstructure_age_ms=0,
    )
    adaptive_context = {
        "portfolio_state": portfolio_state.model_dump(mode="json"),
        "volatility_state": volatility_state.model_dump(mode="json"),
        "iv_snapshot": iv_snapshot,
    }

    provider = build_llm_provider(
        settings,
        vercel_oidc_token=vercel_oidc_token,
    )
    orchestrator = DecisionOrchestrator(
        AgentService(provider),
        DeterministicRiskEngine(settings),
        portfolio_adapter=MetaRLPortfolioAdapterAgent(
            None,
            shadow_mode=True,
            blacklist_probability=settings.v6_blacklist_probability,
            minimum_blacklist_samples=settings.v6_min_blacklist_samples,
            hard_losing_streak=settings.v6_hard_losing_streak,
            hard_mean_net_return=settings.v6_hard_mean_net_return,
        ),
        dynamic_risk_agent=DynamicVolatilityRiskAgent(
            None,
            shadow_mode=True,
            max_microstructure_age_ms=settings.v6_max_microstructure_age_ms,
            adverse_imbalance=settings.v6_adverse_orderbook_imbalance,
        ),
    )

    market_context = {
        "ticker": snapshot["ticker"],
        "orderbook": snapshot["orderbook"],
        "market_data_age_ms": snapshot["market_data_age_ms"],
        "timeframes": timeframe_features,
        "strategy_evidence": signal_payload,
    }
    try:
        result = await orchestrator.decide(
            symbol=display_symbol,
            market_context=market_context,
            quant_features=primary,
            ml_evidence=signal_payload,
            account_state=account_state,
            allowed_levels=allowed_levels,
            adaptive_context=adaptive_context,
        )
    except LLMFailure as exc:
        return {
            "symbol": display_symbol,
            "v51_signal": signal_payload,
            "outcome": "NO_TRADE",
            "reason": "llm_provider_failure",
            "error": type(exc).__name__,
            "paper_only": True,
            "persisted": False,
            "execution_performed": False,
        }

    risk = result.get("risk") or {}
    raw_outcome = str(result.get("outcome") or "NO_TRADE")
    protocol_direction_match = raw_outcome == live_signal.signal
    risk_approved = bool(risk.get("approved"))
    candidate = bool(
        raw_outcome in {"LONG", "SHORT"}
        and protocol_direction_match
        and risk_approved
    )

    return {
        "symbol": display_symbol,
        "v51_signal": signal_payload,
        "market_snapshot_meta": {
            "last_price": snapshot["ticker"]["last_price"],
            "market_data_age_ms": snapshot["market_data_age_ms"],
            "bybit_orderbook_imbalance": snapshot["orderbook"]["imbalance"],
            "binance_orderbook_imbalance": binance_imbalance,
            "implied_volatility": iv_snapshot["mark_iv"],
        },
        "allowed_trade_levels": {
            "long": allowed_levels.long,
            "short": allowed_levels.short,
        },
        "agent_result": result,
        "protocol_gate": {
            "v51_direction_match": protocol_direction_match,
            "deterministic_risk_approved": risk_approved,
            "research_paper_candidate": candidate,
            "execution_authority": False,
        },
        "outcome": raw_outcome if candidate else "NO_TRADE",
        "paper_only": True,
        "persisted": False,
        "execution_performed": False,
        "v6_enforcement": False,
        "observed_at": observed_at.isoformat(),
    }
