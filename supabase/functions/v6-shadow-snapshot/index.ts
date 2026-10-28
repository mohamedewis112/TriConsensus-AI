import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SECRET_KEYS = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
const SECRET_KEY = SECRET_KEYS["default"] || Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const supabase = createClient(SUPABASE_URL, SECRET_KEY, {
  auth: { persistSession: false, autoRefreshToken: false },
});

const displaySymbol = (symbol: string) =>
  symbol === "BTCUSDT" ? "BTC/USDT" : "ETH/USDT";

function depthMetrics(bids: string[][], asks: string[][]) {
  if (!bids.length || !asks.length) throw new Error("empty orderbook");
  const bidDepth = bids.reduce((sum, row) => sum + Number(row[1]), 0);
  const askDepth = asks.reduce((sum, row) => sum + Number(row[1]), 0);
  const total = bidDepth + askDepth;
  const imbalance = total > 0 ? (bidDepth - askDepth) / total : 0;
  const bestBid = Number(bids[0][0]);
  const bestAsk = Number(asks[0][0]);
  const mid = (bestBid + bestAsk) / 2;
  const spreadBps = mid > 0 ? ((bestAsk - bestBid) / mid) * 10000 : 0;
  return { bidDepth, askDepth, imbalance, spreadBps };
}

async function checksum(payload: unknown) {
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function persistMicrostructure(
  venue: "bybit" | "binance",
  symbol: string,
  exchangeTimestampMs: number,
  sequence: number | null,
  bids: string[][],
  asks: string[][],
) {
  const receivedTimestampMs = Date.now();
  const { bidDepth, askDepth, imbalance, spreadBps } = depthMetrics(bids, asks);
  const payload = {
    venue,
    symbol: displaySymbol(symbol),
    exchange_timestamp_ms: exchangeTimestampMs,
    received_timestamp_ms: receivedTimestampMs,
    sequence,
    bid_depth: bidDepth,
    ask_depth: askDepth,
    imbalance,
    spread_bps: spreadBps,
  };
  const digest = await checksum(payload);
  const capturedAt = new Date(receivedTimestampMs).toISOString();

  const latest = {
    ...payload,
    captured_at: capturedAt,
    payload: {
      collector: "supabase_edge_rest_fallback",
      state_kind: "latest",
      expected_cadence_seconds: 60,
    },
    checksum: digest,
    updated_at: new Date().toISOString(),
  };

  const { error: latestError } = await supabase
    .from("v6_microstructure_latest")
    .upsert(latest, { onConflict: "venue,symbol" });
  if (latestError) throw latestError;

  const history = {
    ...payload,
    captured_at: capturedAt,
    payload: {
      collector: "supabase_edge_rest_fallback",
      state_kind: "history",
      expected_cadence_seconds: 60,
    },
    checksum: digest,
  };
  const { error: historyError } = await supabase
    .from("v6_microstructure_snapshots")
    .insert(history);
  if (historyError && !String(historyError.message || "").includes("duplicate")) {
    throw historyError;
  }
}

async function collectBybit(symbol: string) {
  const url = new URL("https://api.bybit.com/v5/market/orderbook");
  url.searchParams.set("category", "linear");
  url.searchParams.set("symbol", symbol);
  url.searchParams.set("limit", "50");
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Bybit HTTP ${response.status}`);
  const body = await response.json();
  if (body.retCode !== 0) throw new Error(`Bybit: ${body.retMsg}`);
  const result = body.result || {};
  await persistMicrostructure(
    "bybit",
    symbol,
    Number(result.cts || result.ts || body.time || Date.now()),
    result.seq == null ? null : Number(result.seq),
    result.b || [],
    result.a || [],
  );
}

async function collectBinance(symbol: string) {
  const url = new URL("https://fapi.binance.com/fapi/v1/depth");
  url.searchParams.set("symbol", symbol);
  url.searchParams.set("limit", "20");
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Binance HTTP ${response.status}`);
  const body = await response.json();
  await persistMicrostructure(
    "binance",
    symbol,
    Number(body.E || body.T || Date.now()),
    body.lastUpdateId == null ? null : Number(body.lastUpdateId),
    body.bids || [],
    body.asks || [],
  );
}

const MONTHS: Record<string, number> = {
  JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
  JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
};

function parseExpiry(raw: string): number | null {
  const match = raw.toUpperCase().match(/^(\d{1,2})([A-Z]{3})(\d{2})$/);
  if (!match || MONTHS[match[2]] == null) return null;
  return Date.UTC(2000 + Number(match[3]), MONTHS[match[2]], Number(match[1]));
}

async function collectIv(symbol: string) {
  const baseCoin = symbol.startsWith("BTC") ? "BTC" : "ETH";
  const url = new URL("https://api.bybit.com/v5/market/tickers");
  url.searchParams.set("category", "option");
  url.searchParams.set("baseCoin", baseCoin);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Bybit IV HTTP ${response.status}`);
  const body = await response.json();
  if (body.retCode !== 0) throw new Error(`Bybit IV: ${body.retMsg}`);
  const rows = body.result?.list || [];
  const now = Date.now();

  const candidates: Array<{
    expiryMs: number;
    markIv: number;
    openInterest: number;
  }> = [];

  for (const item of rows) {
    const parts = String(item.symbol || "").split("-");
    if (parts.length < 4) continue;
    const expiryMs = parseExpiry(parts[1]);
    const markIv = Number(item.markIv || 0);
    const delta = Math.abs(Number(item.delta || 0));
    const openInterest = Number(item.openInterest || 0);
    if (
      expiryMs == null ||
      expiryMs <= now ||
      markIv <= 0 ||
      delta < 0.25 ||
      delta > 0.75 ||
      openInterest <= 0
    ) continue;
    candidates.push({ expiryMs, markIv, openInterest });
  }

  if (!candidates.length) throw new Error(`No liquid IV for ${baseCoin}`);
  const nearest = Math.min(...candidates.map((x) => x.expiryMs));
  const front = candidates.filter((x) => x.expiryMs === nearest);
  const totalOi = front.reduce((sum, x) => sum + x.openInterest, 0);
  const markIv = front.reduce((sum, x) => sum + x.markIv * x.openInterest, 0) / totalOi;

  const { error } = await supabase.from("v6_volatility_snapshots").insert({
    symbol: displaySymbol(symbol),
    source: "bybit_option_tickers_mark_iv_edge_fallback",
    captured_at: new Date().toISOString(),
    mark_iv: markIv,
    expiry: new Date(nearest).toISOString(),
    sample_count: front.length,
    payload: {
      collector: "supabase_edge_rest_fallback",
      base_coin: baseCoin,
      open_interest_weight: totalOi,
      expected_cadence_seconds: 300,
    },
  });
  if (error) throw error;
}

Deno.serve(async (req: Request) => {
  const token = req.headers.get("x-v6-token") || "";
  const { data: valid, error: authError } = await supabase.rpc(
    "v6_validate_shadow_collector_token",
    { p_token: token },
  );
  if (authError || valid !== true) {
    return Response.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }

  let forceIv = false;
  try {
    const body = await req.json();
    forceIv = body?.force_iv === true;
  } catch {
    forceIv = false;
  }

  const tasks: Array<Promise<void>> = [
    collectBybit("BTCUSDT"),
    collectBybit("ETHUSDT"),
    collectBinance("BTCUSDT"),
    collectBinance("ETHUSDT"),
  ];

  const minute = new Date().getUTCMinutes();
  if (forceIv || minute % 5 === 0) {
    tasks.push(collectIv("BTCUSDT"), collectIv("ETHUSDT"));
  }

  const results = await Promise.allSettled(tasks);
  const failures = results
    .map((result, index) => ({ result, index }))
    .filter(({ result }) => result.status === "rejected")
    .map(({ result, index }) => ({
      index,
      error: result.status === "rejected" ? String(result.reason) : "",
    }));

  return Response.json({
    ok: failures.length === 0,
    attempted: tasks.length,
    succeeded: tasks.length - failures.length,
    failures,
    mode: "V6_SHADOW_REST_FALLBACK",
    trading_authority: false,
  });
});
