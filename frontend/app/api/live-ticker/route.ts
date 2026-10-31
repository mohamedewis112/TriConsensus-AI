import {NextResponse} from "next/server";

export const dynamic = "force-dynamic";

type BybitTicker = {
  symbol?: string;
  lastPrice?: string;
  markPrice?: string;
  indexPrice?: string;
  bid1Price?: string;
  ask1Price?: string;
  fundingRate?: string;
  openInterest?: string;
  price24hPcnt?: string;
  highPrice24h?: string;
  lowPrice24h?: string;
  volume24h?: string;
};

async function ticker(symbol: "BTCUSDT" | "ETHUSDT") {
  const url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=" + symbol;
  const response = await fetch(url, {cache: "no-store"});
  if (!response.ok) throw new Error("Bybit ticker unavailable");
  const payload = await response.json() as {
    retCode?: number;
    result?: {list?: BybitTicker[]};
    time?: number;
  };
  if (payload.retCode !== 0 || !payload.result?.list?.[0]) throw new Error("Invalid Bybit response");
  return payload.result.list[0];
}

export async function GET() {
  try {
    const [btc, eth] = await Promise.all([ticker("BTCUSDT"), ticker("ETHUSDT")]);
    return NextResponse.json(
      {source: "Bybit public V5 ticker", fetched_at: new Date().toISOString(), BTCUSDT: btc, ETHUSDT: eth},
      {headers: {"Cache-Control": "no-store, max-age=0"}},
    );
  } catch {
    return NextResponse.json(
      {error: "live ticker unavailable"},
      {status: 503, headers: {"Cache-Control": "no-store"}},
    );
  }
}
