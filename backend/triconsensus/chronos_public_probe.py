from __future__ import annotations

import asyncio
import json

import httpx


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> object:
    response = await client.get(url, params=params or {})
    response.raise_for_status()
    return response.json()


async def main() -> None:
    timeout = httpx.Timeout(8.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent":"TriConsensus-Research/1.0"}) as client:
        report = {}
        for asset in ("BTC", "ETH"):
            symbol = f"{asset}USDT"
            okx_id = f"{asset}-USDT-SWAP"
            coinbase_id = f"{asset}-USD"
            symbol_report = {"okx": {}, "coinbase": {}}

            for name, url, params in (
                ("depth","https://www.okx.com/api/v5/market/books",{"instId":okx_id,"sz":20}),
                ("candles","https://www.okx.com/api/v5/market/candles",{"instId":okx_id,"bar":"1m","limit":10}),
                ("funding","https://www.okx.com/api/v5/public/funding-rate",{"instId":okx_id}),
                ("open_interest","https://www.okx.com/api/v5/public/open-interest",{"instType":"SWAP","instId":okx_id}),
            ):
                try:
                    body = await _get(client,url,params)
                    ok = isinstance(body,dict) and str(body.get("code",""))=="0" and bool(body.get("data"))
                    symbol_report["okx"][name]={"ok":ok,"code":body.get("code") if isinstance(body,dict) else None,"data_len":len(body.get("data") or []) if isinstance(body,dict) else 0}
                except Exception as exc:
                    symbol_report["okx"][name]={"ok":False,"error_type":type(exc).__name__,"error":str(exc)}

            for name,url,params in (
                ("depth",f"https://api.exchange.coinbase.com/products/{coinbase_id}/book",{"level":2}),
                ("ticker",f"https://api.exchange.coinbase.com/products/{coinbase_id}/ticker",{}),
                ("candles",f"https://api.exchange.coinbase.com/products/{coinbase_id}/candles",{"granularity":60}),
            ):
                try:
                    body=await _get(client,url,params)
                    if name=="depth":
                        ok=isinstance(body,dict) and bool(body.get("bids")) and bool(body.get("asks"))
                    elif name=="candles":
                        ok=isinstance(body,list) and len(body)>=6
                    else:
                        ok=isinstance(body,dict) and bool(body.get("price"))
                    symbol_report["coinbase"][name]={"ok":ok,"shape":type(body).__name__}
                except Exception as exc:
                    symbol_report["coinbase"][name]={"ok":False,"error_type":type(exc).__name__,"error":str(exc)}

            report[symbol]=symbol_report

        print(json.dumps(report,indent=2,sort_keys=True))
        required=[]
        for symbol_report in report.values():
            required.extend(item["ok"] for item in symbol_report["okx"].values())
            required.extend(item["ok"] for item in symbol_report["coinbase"].values())
        if not all(required):
            raise RuntimeError("OKX_COINBASE_PUBLIC_SOURCE_PROBE_FAILED")


if __name__=="__main__":
    asyncio.run(main())
