from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_PAIRS = {"BTC/USDT:USDT", "ETH/USDT:USDT"}


@dataclass(frozen=True)
class FreqtradeSafetyStatus:
    safe: bool
    reason: str
    force_entry_enabled: bool = False


def _exchange_name(config: dict[str, Any]) -> str:
    exchange = config.get("exchange")
    if isinstance(exchange, dict):
        return str(exchange.get("name") or "").lower()
    return str(config.get("exchange_name") or exchange or "").lower()


def _whitelist(config: dict[str, Any]) -> set[str]:
    exchange = config.get("exchange")
    if isinstance(exchange, dict):
        raw = exchange.get("pair_whitelist") or []
    else:
        raw = config.get("pair_whitelist") or config.get("whitelist") or []
    return {str(pair) for pair in raw}


def validate_freqtrade_runtime(config: dict[str, Any]) -> FreqtradeSafetyStatus:
    """
    Strict runtime guard for TriConsensus V1.

    Unknown/missing safety-critical fields fail closed. This function is intended
    for the response returned by Freqtrade's authenticated show_config endpoint
    or for the fully merged local configuration.
    """
    if config.get("dry_run") is not True:
        return FreqtradeSafetyStatus(False, "dry_run_not_enabled")

    if str(config.get("trading_mode") or "").lower() != "futures":
        return FreqtradeSafetyStatus(False, "not_futures_mode")

    if str(config.get("margin_mode") or "").lower() != "isolated":
        return FreqtradeSafetyStatus(False, "margin_mode_not_isolated")

    if _exchange_name(config) != "bybit":
        return FreqtradeSafetyStatus(False, "exchange_not_bybit")

    max_open = config.get("max_open_trades")
    if not isinstance(max_open, int) or max_open < 1 or max_open > 1:
        return FreqtradeSafetyStatus(False, "max_open_trades_not_one")

    pairs = _whitelist(config)
    if not pairs:
        return FreqtradeSafetyStatus(False, "pair_whitelist_missing")
    if not pairs.issubset(ALLOWED_PAIRS):
        return FreqtradeSafetyStatus(False, "unapproved_pair_in_whitelist")

    force_entry = config.get("force_entry_enable") is True
    return FreqtradeSafetyStatus(
        True,
        "safe_dry_run_configuration",
        force_entry_enabled=force_entry,
    )


def assert_freqtrade_safe(config: dict[str, Any], *, require_force_entry: bool = False) -> None:
    status = validate_freqtrade_runtime(config)
    if not status.safe:
        raise RuntimeError(f"Freqtrade safety check failed: {status.reason}")
    if require_force_entry and not status.force_entry_enabled:
        raise RuntimeError("Freqtrade safety check failed: force_entry_disabled")
