from __future__ import annotations

import asyncio
import json

import httpx

from .chronos_public_live import _fetch_context, _fetch_depth


async def main() -> None:
    timeout = httpx.Timeout(8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        report = {}
        for symbol in ("BTCUSDT", "ETHUSDT"):
            symbol_report = {}
            try:
                context = await _fetch_context(client, symbol)
                symbol_report["context"] = {
                    "ok": True,
                    "funding_rate": context["funding_rate"],
                    "open_interest": context["open_interest"],
                    "close_1m": context["close_1m"],
                    "close_5m": context["close_5m"],
                }
            except Exception as exc:
                symbol_report["context"] = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            try:
                depth = await _fetch_depth(client, symbol)
                symbol_report["depth"] = {
                    "ok": True,
                    "bybit_mid": depth["bybit_mid"],
                    "binance_mid": depth["binance_mid"],
                    "basis_bps": depth["basis_bps"],
                }
            except Exception as exc:
                symbol_report["depth"] = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            report[symbol] = symbol_report
        print(json.dumps(report, indent=2, sort_keys=True))
        if not all(
            report[symbol]["context"]["ok"] and report[symbol]["depth"]["ok"]
            for symbol in report
        ):
            raise RuntimeError("PUBLIC_MARKET_PROBE_FAILED")


if __name__ == "__main__":
    asyncio.run(main())
