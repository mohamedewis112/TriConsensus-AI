from __future__ import annotations

import asyncio
import json

import httpx


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


async def main() -> None:
    timeout = httpx.Timeout(8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        report = {}
        for symbol in ("BTCUSDT", "ETHUSDT"):
            asset = symbol.replace("USDT", "")
            okx_id = f"{asset}-USDT-SWAP"
            symbol_report = {"binance": {}, "okx": {}}

            for name, url, params in (
                (
                    "depth",
                    "https://fapi.binance.com/fapi/v1/depth",
                    {"symbol": symbol, "limit": 20},
                ),
                (
                    "klines",
                    "https://fapi.binance.com/fapi/v1/klines",
                    {"symbol": symbol, "interval": "1m", "limit": 10},
                ),
                (
                    "funding",
                    "https://fapi.binance.com/fapi/v1/premiumIndex",
                    {"symbol": symbol},
                ),
                (
                    "open_interest",
                    "https://fapi.binance.com/fapi/v1/openInterest",
                    {"symbol": symbol},
                ),
            ):
                try:
                    body = await _get(client, url, params)
                    symbol_report["binance"][name] = {
                        "ok": True,
                        "shape": type(body).__name__,
                    }
                except Exception as exc:
                    symbol_report["binance"][name] = {
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }

            for name, url, params in (
                (
                    "depth",
                    "https://www.okx.com/api/v5/market/books",
                    {"instId": okx_id, "sz": 20},
                ),
                (
                    "candles",
                    "https://www.okx.com/api/v5/market/candles",
                    {"instId": okx_id, "bar": "1m", "limit": 10},
                ),
                (
                    "funding",
                    "https://www.okx.com/api/v5/public/funding-rate",
                    {"instId": okx_id},
                ),
                (
                    "open_interest",
                    "https://www.okx.com/api/v5/public/open-interest",
                    {"instType": "SWAP", "instId": okx_id},
                ),
            ):
                try:
                    body = await _get(client, url, params)
                    ok = str(body.get("code", "")) == "0" and bool(body.get("data"))
                    symbol_report["okx"][name] = {
                        "ok": ok,
                        "code": body.get("code"),
                        "msg": body.get("msg"),
                        "data_len": len(body.get("data") or []),
                    }
                except Exception as exc:
                    symbol_report["okx"][name] = {
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }

            report[symbol] = symbol_report

        print(json.dumps(report, indent=2, sort_keys=True))
        required = []
        for symbol_report in report.values():
            required.extend(item["ok"] for item in symbol_report["binance"].values())
            required.extend(item["ok"] for item in symbol_report["okx"].values())
        if not all(required):
            raise RuntimeError("CROSS_VENUE_PUBLIC_SOURCE_PROBE_FAILED")


if __name__ == "__main__":
    asyncio.run(main())
