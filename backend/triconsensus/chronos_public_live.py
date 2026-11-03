from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import time

import httpx
import numpy as np

from .chronos import MarketState
from .chronos_hybrid import ChronosArtificialMarketCivilization
from .chronos_learning import transitions_from_states
from .chronos_memory import MarketMemoryIndex
from .v6_microstructure import depth_metrics


BYBIT = "https://api.bytick.com"
BINANCE = "https://fapi.binance.com"


class PublicLiveShadowFailure(RuntimeError):
    pass


def _bybit_result(body: dict) -> dict:
    if body.get("retCode") != 0:
        raise PublicLiveShadowFailure(f"Bybit error: {body.get('retMsg')}")
    return body.get("result") or {}


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


async def _fetch_depth(client: httpx.AsyncClient, symbol: str) -> dict:
    asset = symbol.replace("USDT", "")
    okx_id = f"{asset}-USDT-SWAP"
    coinbase_id = f"{asset}-USD"
    okx_body, coinbase_body = await asyncio.gather(
        _get_json(
            client,
            f"{OKX}/api/v5/market/books",
            {"instId": okx_id, "sz": 20},
        ),
        _get_json(
            client,
            f"{COINBASE}/products/{coinbase_id}/book",
            {"level": 2},
        ),
    )
    if str(okx_body.get("code", "")) != "0" or not okx_body.get("data"):
        raise PublicLiveShadowFailure(f"OKX depth error: {okx_body.get('msg')}")
    okx_book = okx_body["data"][0]
    o_bids = okx_book.get("bids") or []
    o_asks = okx_book.get("asks") or []
    c_bids = coinbase_body.get("bids") or []
    c_asks = coinbase_body.get("asks") or []
    okx_metrics = depth_metrics(o_bids, o_asks)
    coinbase_metrics = depth_metrics(c_bids, c_asks)
    okx_mid = (float(o_bids[0][0]) + float(o_asks[0][0])) / 2.0
    coinbase_mid = (float(c_bids[0][0]) + float(c_asks[0][0])) / 2.0
    return {
        "okx": okx_metrics,
        "coinbase": coinbase_metrics,
        "okx_mid": okx_mid,
        "coinbase_mid": coinbase_mid,
        "basis_bps": ((okx_mid - coinbase_mid) / ((okx_mid + coinbase_mid) / 2.0)) * 10_000,
    }


async def _fetch_context(client: httpx.AsyncClient, symbol: str) -> dict:
    asset = symbol.replace("USDT", "")
    okx_id = f"{asset}-USDT-SWAP"
    candles_body, funding_body, oi_body = await asyncio.gather(
        _get_json(
            client,
            f"{OKX}/api/v5/market/candles",
            {"instId": okx_id, "bar": "1m", "limit": 10},
        ),
        _get_json(
            client,
            f"{OKX}/api/v5/public/funding-rate",
            {"instId": okx_id},
        ),
        _get_json(
            client,
            f"{OKX}/api/v5/public/open-interest",
            {"instType": "SWAP", "instId": okx_id},
        ),
    )
    for name, body in (("candles", candles_body), ("funding", funding_body), ("open_interest", oi_body)):
        if str(body.get("code", "")) != "0" or not body.get("data"):
            raise PublicLiveShadowFailure(f"OKX {name} error: {body.get('msg')}")
    rows = candles_body["data"]
    closed = sorted(
        [row for row in rows if len(row) < 9 or str(row[8]) == "1"],
        key=lambda row: int(row[0]),
    )
    if len(closed) < 6:
        raise PublicLiveShadowFailure("insufficient closed OKX 1m candles")
    close_1m = float(closed[-1][4])
    close_5m = float(closed[-5][4])
    funding_rate = float(funding_body["data"][0].get("fundingRate") or 0.0)
    open_interest = float(oi_body["data"][0].get("oi") or 0.0)
    return {
        "funding_rate": funding_rate,
        "open_interest": open_interest,
        "close_1m": close_1m,
        "close_5m": close_5m,
        "captured_ms": int(time.time() * 1000),
    }
