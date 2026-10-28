import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

const ALLOWED_SYMBOLS = new Set(["BTCUSDT", "ETHUSDT"]);
const MAX_SPAN_MS = 7 * 24 * 60 * 60 * 1000;
const SLOT_MS = 15 * 60 * 1000;
const FIVE_MINUTES_MS = 5 * 60 * 1000;
const BINANCE_START_LABEL_CUTOFF_MS = 1782345600000;
const BINANCE_LIVE_HISTORY_MS = 30 * 24 * 60 * 60 * 1000;

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

async function bybitGet(path: string, params: Record<string, string | number>) {
  const url = new URL("https://api.bybit.com" + path);
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, String(value));
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12_000);
  try {
    const res = await fetch(url, {
      headers: { "user-agent": "TriConsensus-AI/0.2 derivatives-collector" },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error("BYBIT_HTTP_" + res.status);
    const payload = await res.json();
    if (payload?.retCode !== 0 || typeof payload?.result !== "object") {
      throw new Error("BYBIT_RETCODE_" + String(payload?.retCode ?? "missing"));
    }
    return payload.result;
  } finally {
    clearTimeout(timer);
  }
}

async function klineHistory(symbol: string, startMs: number, endMs: number) {
  const result = await bybitGet("/v5/market/kline", {
    category: "linear",
    symbol,
    interval: "15",
    start: startMs,
    end: endMs,
    limit: 1000,
  });
  const rows = Array.isArray(result.list) ? result.list : [];
  const candles = new Map<number, {
    timestamp_ms: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    turnover: number;
  }>();

  for (const row of rows) {
    if (!Array.isArray(row) || row.length < 7) throw new Error("INVALID_KLINE_ROW");
    const timestamp_ms = Number(row[0]);
    const open = Number(row[1]);
    const high = Number(row[2]);
    const low = Number(row[3]);
    const close = Number(row[4]);
    const volume = Number(row[5]);
    const turnover = Number(row[6]);

    const values = [timestamp_ms, open, high, low, close, volume, turnover];
    if (!values.every(Number.isFinite)) throw new Error("INVALID_KLINE_VALUE");
    if (
      timestamp_ms < startMs ||
      timestamp_ms > endMs
    ) continue;
    if (
      open <= 0 ||
      high <= 0 ||
      low <= 0 ||
      close <= 0 ||
      volume < 0 ||
      turnover < 0 ||
      high < low ||
      high < Math.max(open, close) ||
      low > Math.min(open, close)
    ) {
      throw new Error("INVALID_KLINE_GEOMETRY");
    }

    candles.set(timestamp_ms, {
      timestamp_ms,
      open,
      high,
      low,
      close,
      volume,
      turnover,
    });
  }

  return [...candles.values()].sort((a, b) => a.timestamp_ms - b.timestamp_ms);
}

async function premiumIndexHistory(symbol: string, startMs: number, endMs: number) {
  const result = await bybitGet("/v5/market/premium-index-price-kline", {
    category: "linear",
    symbol,
    interval: "15",
    start: startMs,
    end: endMs,
    limit: 1000,
  });
  const rows = Array.isArray(result.list) ? result.list : [];
  const points = new Map<number, {
    timestamp_ms: number;
    open: number;
    high: number;
    low: number;
    close: number;
  }>();

  for (const row of rows) {
    if (!Array.isArray(row) || row.length < 5) throw new Error("INVALID_PREMIUM_ROW");
    const timestamp_ms = Number(row[0]);
    const open = Number(row[1]);
    const high = Number(row[2]);
    const low = Number(row[3]);
    const close = Number(row[4]);
    const values = [timestamp_ms, open, high, low, close];
    if (!values.every(Number.isFinite)) throw new Error("INVALID_PREMIUM_VALUE");
    if (timestamp_ms < startMs || timestamp_ms > endMs) continue;
    if (high < low) {
      throw new Error("INVALID_PREMIUM_GEOMETRY");
    }
    points.set(timestamp_ms, { timestamp_ms, open, high, low, close });
  }

  return [...points.values()].sort((a, b) => a.timestamp_ms - b.timestamp_ms);
}

async function fundingHistory(symbol: string, startMs: number, endMs: number) {
  const points = new Map<number, number>();
  let pageEnd = endMs;
  let previousOldest: number | null = null;

  for (let page = 0; page < 100; page++) {
    const result = await bybitGet("/v5/market/funding/history", {
      category: "linear",
      symbol,
      endTime: pageEnd,
      limit: 200,
    });
    const rows = Array.isArray(result.list) ? result.list : [];
    if (rows.length === 0) break;

    const timestamps: number[] = [];
    for (const row of rows) {
      const ts = Number(row.fundingRateTimestamp);
      const rate = Number(row.fundingRate);
      if (!Number.isFinite(ts) || !Number.isFinite(rate)) {
        throw new Error("INVALID_FUNDING_ROW");
      }
      timestamps.push(ts);
      if (ts >= startMs && ts <= endMs) points.set(ts, rate);
    }

    const oldest = Math.min(...timestamps);
    if (oldest <= startMs) break;
    if (previousOldest !== null && oldest >= previousOldest) {
      throw new Error("FUNDING_PAGINATION_STALLED");
    }
    previousOldest = oldest;
    pageEnd = oldest - 1;
  }
  return [...points.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([timestamp_ms, funding_rate]) => ({ timestamp_ms, funding_rate }));
}

async function cursorHistory(
  path: string,
  baseParams: Record<string, string | number>,
  startMs: number,
  endMs: number,
  limit: number,
) {
  const rowsByTs = new Map<number, Record<string, unknown>>();
  let cursor = "";
  const seen = new Set<string>();

  for (let page = 0; page < 500; page++) {
    const params: Record<string, string | number> = {
      ...baseParams,
      startTime: startMs,
      endTime: endMs,
      limit,
    };
    if (cursor) params.cursor = cursor;

    const result = await bybitGet(path, params);
    const rows = Array.isArray(result.list) ? result.list : [];
    for (const row of rows) {
      const ts = Number(row.timestamp);
      if (!Number.isFinite(ts)) throw new Error("INVALID_TIMESTAMP_ROW");
      if (ts >= startMs && ts <= endMs) rowsByTs.set(ts, row);
    }

    const next = String(result.nextPageCursor ?? "");
    if (!next) break;
    if (seen.has(next)) throw new Error("CURSOR_REPEATED");
    seen.add(next);
    cursor = next;
  }

  return [...rowsByTs.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([timestamp_ms, row]) => ({ timestamp_ms, row }));
}

async function binanceGlobalRatioHistory(
  symbol: string,
  startMs: number,
  endMs: number,
) {
  const url = new URL(
    "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
  );
  url.searchParams.set("symbol", symbol);
  url.searchParams.set("period", "5m");
  url.searchParams.set("startTime", String(Math.max(1, startMs - FIVE_MINUTES_MS)));
  url.searchParams.set("endTime", String(endMs));
  url.searchParams.set("limit", "500");

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12_000);
  try {
    const res = await fetch(url, {
      headers: {
        "user-agent": "TriConsensus-AI/0.3 cross-venue-collector",
      },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error("BINANCE_HTTP_" + res.status);
    const payload = await res.json();
    if (!Array.isArray(payload)) throw new Error("BINANCE_INVALID_PAYLOAD");

    const points = new Map<number, {
      timestamp_ms: number;
      long_short_ratio: number;
      long_account: number;
      short_account: number;
    }>();

    for (const row of payload) {
      const rawTimestamp = Number(row?.timestamp);
      const longShortRatio = Number(row?.longShortRatio);
      const longAccount = Number(row?.longAccount);
      const shortAccount = Number(row?.shortAccount);
      if (
        !Number.isFinite(rawTimestamp) ||
        !Number.isFinite(longShortRatio) ||
        !Number.isFinite(longAccount) ||
        !Number.isFinite(shortAccount) ||
        rawTimestamp <= 0 ||
        longShortRatio <= 0 ||
        longAccount < 0 ||
        longAccount > 1 ||
        shortAccount < 0 ||
        shortAccount > 1
      ) {
        throw new Error("BINANCE_INVALID_RATIO_ROW");
      }

      const timestampMs = rawTimestamp >= BINANCE_START_LABEL_CUTOFF_MS
        ? rawTimestamp + FIVE_MINUTES_MS
        : rawTimestamp;
      if (timestampMs < startMs || timestampMs > endMs) continue;

      points.set(timestampMs, {
        timestamp_ms: timestampMs,
        long_short_ratio: longShortRatio,
        long_account: longAccount,
        short_account: shortAccount,
      });
    }
    return [...points.values()].sort((a, b) => a.timestamp_ms - b.timestamp_ms);
  } finally {
    clearTimeout(timer);
  }
}

async function upsertChunks(
  supabase: ReturnType<typeof createClient>,
  table: string,
  rows: Record<string, unknown>[],
  onConflict: string,
  ignoreDuplicates = false,
) {
  for (let i = 0; i < rows.length; i += 500) {
    const chunk = rows.slice(i, i + 500);
    const { error } = await supabase
      .from(table)
      .upsert(chunk, { onConflict, ignoreDuplicates });
    if (error) throw new Error("DB_UPSERT_" + table + "_" + error.code);
  }
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return response({ ok: false, error: "method_not_allowed" }, 405);

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRole) {
    return response({ ok: false, error: "server_configuration_missing" }, 500);
  }

  const supabase = createClient(supabaseUrl, serviceRole, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  const suppliedKey = req.headers.get("x-triconsensus-collector") ?? "";
  const { data: control, error: controlError } = await supabase
    .from("collector_control")
    .select("secret_sha256,active")
    .eq("collector_name", "derivatives_v1")
    .maybeSingle();

  if (
    controlError ||
    !control ||
    control.active !== true ||
    !suppliedKey ||
    !safeEqual(await sha256Hex(suppliedKey), String(control.secret_sha256))
  ) {
    return response({ ok: false, error: "unauthorized" }, 401);
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return response({ ok: false, error: "invalid_json" }, 400);
  }

  const symbol = String(body.symbol ?? "");
  const startMs = Number(body.start_ms);
  const endMs = Number(body.end_ms);
  const queueId = String(body.queue_id ?? "");

  if (!ALLOWED_SYMBOLS.has(symbol)) {
    return response({ ok: false, error: "unsupported_symbol" }, 400);
  }
  if (
    !Number.isInteger(startMs) ||
    !Number.isInteger(endMs) ||
    startMs <= 0 ||
    endMs <= startMs ||
    endMs - startMs > MAX_SPAN_MS
  ) {
    return response({ ok: false, error: "invalid_time_range_or_span_gt_7d" }, 400);
  }
  if (
    queueId &&
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(queueId)
  ) {
    return response({ ok: false, error: "invalid_queue_id" }, 400);
  }

  let runId: string | null = null;
  try {
    if (queueId) {
      const { data: queueRow, error: queueError } = await supabase
        .from("derivatives_backfill_queue")
        .update({
          status: "running",
          updated_at: new Date().toISOString(),
          last_error: null,
        })
        .eq("id", queueId)
        .eq("symbol", symbol)
        .in("status", ["dispatched", "running"])
        .select("id")
        .maybeSingle();
      if (queueError || !queueRow) throw new Error("QUEUE_NOT_DISPATCHED");
    }

    const { data: run, error: runError } = await supabase
      .from("derivatives_collection_runs")
      .insert({
        symbol,
        requested_start: new Date(startMs).toISOString(),
        requested_end: new Date(endMs).toISOString(),
        status: "running",
      })
      .select("id")
      .single();
    if (runError || !run) throw new Error("DB_CREATE_RUN");
    runId = String(run.id);

    const fundingLookbackStart = Math.max(
      1,
      startMs - (24 * 60 * 60 * 1000),
    );
    const [funding, oiRaw, ratioRaw, candles, premiumIndex] = await Promise.all([
      fundingHistory(symbol, fundingLookbackStart, endMs),
      cursorHistory(
        "/v5/market/open-interest",
        { category: "linear", symbol, intervalTime: "15min" },
        startMs,
        endMs,
        200,
      ),
      cursorHistory(
        "/v5/market/account-ratio",
        { category: "linear", symbol, period: "15min" },
        startMs,
        endMs,
        500,
      ),
      klineHistory(symbol, startMs, endMs),
      premiumIndexHistory(symbol, startMs, endMs),
    ]);

    const binanceLiveRequired =
      endMs >= Date.now() - BINANCE_LIVE_HISTORY_MS;
    const binanceGlobalRatio = binanceLiveRequired
      ? await binanceGlobalRatioHistory(symbol, startMs, endMs)
      : [];

    const oi = oiRaw.map(({ timestamp_ms, row }) => {
      const value = Number(row.openInterest);
      if (!Number.isFinite(value) || value < 0) throw new Error("INVALID_OI_ROW");
      return { timestamp_ms, open_interest: value };
    });

    const ratios = ratioRaw.map(({ timestamp_ms, row }) => {
      const buy = Number(row.buyRatio);
      const sell = Number(row.sellRatio);
      if (!Number.isFinite(buy) || !Number.isFinite(sell) || buy < 0 || sell <= 0) {
        throw new Error("INVALID_RATIO_ROW");
      }
      return {
        timestamp_ms,
        buy_ratio: buy,
        sell_ratio: sell,
        long_short_ratio: buy / sell,
      };
    });

    await upsertChunks(
      supabase,
      "market_candles_15m",
      candles.map((row) => ({
        symbol,
        open_time: new Date(row.timestamp_ms).toISOString(),
        open_price: row.open,
        high_price: row.high,
        low_price: row.low,
        close_price: row.close,
        volume: row.volume,
        turnover: row.turnover,
        source: "bybit_v5_public",
      })),
      "symbol,open_time",
    );

    await upsertChunks(
      supabase,
      "market_premium_index_15m",
      premiumIndex.map((row) => ({
        symbol,
        open_time: new Date(row.timestamp_ms).toISOString(),
        open_price: row.open,
        high_price: row.high,
        low_price: row.low,
        close_price: row.close,
        source: "bybit_v5_public",
      })),
      "symbol,open_time",
    );

    await upsertChunks(
      supabase,
      "market_funding_history",
      funding.map((row) => ({
        symbol,
        funding_time: new Date(row.timestamp_ms).toISOString(),
        funding_rate: row.funding_rate,
        source: "bybit_v5_public",
      })),
      "symbol,funding_time",
    );

    await upsertChunks(
      supabase,
      "market_open_interest_history",
      oi.map((row) => ({
        symbol,
        interval_name: "15min",
        observed_at: new Date(row.timestamp_ms).toISOString(),
        open_interest: row.open_interest,
        source: "bybit_v5_public",
      })),
      "symbol,interval_name,observed_at",
    );

    await upsertChunks(
      supabase,
      "market_account_ratio_history",
      ratios.map((row) => ({
        symbol,
        period_name: "15min",
        observed_at: new Date(row.timestamp_ms).toISOString(),
        buy_ratio: row.buy_ratio,
        sell_ratio: row.sell_ratio,
        long_short_ratio: row.long_short_ratio,
        source: "bybit_v5_public",
      })),
      "symbol,period_name,observed_at",
    );

    if (binanceGlobalRatio.length > 0) {
      await upsertChunks(
        supabase,
        "market_binance_global_ratio_5m",
        binanceGlobalRatio.map((row) => ({
          symbol,
          period_name: "5m",
          observed_at: new Date(row.timestamp_ms).toISOString(),
          long_short_ratio: row.long_short_ratio,
          long_account: row.long_account,
          short_account: row.short_account,
          source: "binance_usdm_global_long_short_ratio",
        })),
        "symbol,period_name,observed_at",
        true,
      );
    }

    const firstClosedSlot = Math.ceil(startMs / SLOT_MS) * SLOT_MS;
    const lastClosedSlot = Math.floor(endMs / SLOT_MS) * SLOT_MS;
    const expectedSlots = lastClosedSlot >= firstClosedSlot
      ? Math.floor((lastClosedSlot - firstClosedSlot) / SLOT_MS) + 1
      : 0;
    const oiInClosedSlots = oi.filter(
      (row) => row.timestamp_ms >= firstClosedSlot && row.timestamp_ms <= lastClosedSlot,
    ).length;
    const ratioInClosedSlots = ratios.filter(
      (row) => row.timestamp_ms >= firstClosedSlot && row.timestamp_ms <= lastClosedSlot,
    ).length;
    const candleInClosedSlots = candles.filter(
      (row) => row.timestamp_ms >= firstClosedSlot && row.timestamp_ms <= lastClosedSlot,
    ).length;
    const premiumInClosedSlots = premiumIndex.filter(
      (row) => row.timestamp_ms >= firstClosedSlot && row.timestamp_ms <= lastClosedSlot,
    ).length;
    const oiCoverage = expectedSlots > 0 ? Math.min(1, oiInClosedSlots / expectedSlots) : 0;
    const firstFiveMinuteSlot = Math.ceil(startMs / FIVE_MINUTES_MS) * FIVE_MINUTES_MS;
    const lastFiveMinuteSlot = Math.floor(endMs / FIVE_MINUTES_MS) * FIVE_MINUTES_MS;
    const expectedFiveMinuteSlots = lastFiveMinuteSlot >= firstFiveMinuteSlot
      ? Math.floor(
        (lastFiveMinuteSlot - firstFiveMinuteSlot) / FIVE_MINUTES_MS,
      ) + 1
      : 0;
    const binanceRatioCoverage = expectedFiveMinuteSlots > 0
      ? Math.min(1, binanceGlobalRatio.length / expectedFiveMinuteSlots)
      : 0;
    const ratioCoverage = expectedSlots > 0 ? Math.min(1, ratioInClosedSlots / expectedSlots) : 0;
    const candleCoverage = expectedSlots > 0 ? Math.min(1, candleInClosedSlots / expectedSlots) : 0;
    const premiumCoverage = expectedSlots > 0 ? Math.min(1, premiumInClosedSlots / expectedSlots) : 0;
    const quality = {
      first_closed_slot_ms: firstClosedSlot,
      last_closed_slot_ms: lastClosedSlot,
      expected_15m_slots: expectedSlots,
      candle_coverage: candleCoverage,
      premium_index_coverage: premiumCoverage,
      open_interest_coverage: oiCoverage,
      account_ratio_coverage: ratioCoverage,
      funding_points: funding.length,
      binance_live_required: binanceLiveRequired,
      binance_global_ratio_5m_rows: binanceGlobalRatio.length,
      binance_global_ratio_5m_coverage: binanceRatioCoverage,
      qualification_ready:
        expectedSlots > 0 &&
        candleCoverage >= 0.90 &&
        premiumCoverage >= 0.90 &&
        oiCoverage >= 0.90 &&
        ratioCoverage >= 0.90 &&
        funding.length > 0 &&
        (!binanceLiveRequired || binanceRatioCoverage >= 0.90),
    };

    const { error: completeError } = await supabase
      .from("derivatives_collection_runs")
      .update({
        completed_at: new Date().toISOString(),
        status: "completed",
        candle_rows: candles.length,
        premium_index_rows: premiumIndex.length,
        funding_rows: funding.length,
        open_interest_rows: oi.length,
        account_ratio_rows: ratios.length,
        quality,
        error_code: null,
      })
      .eq("id", runId);
    if (completeError) throw new Error("DB_COMPLETE_RUN");

    if (queueId) {
      const { error: queueCompleteError } = await supabase
        .from("derivatives_backfill_queue")
        .update({
          status: "completed",
          collection_run_id: runId,
          quality,
          last_error: null,
          updated_at: new Date().toISOString(),
        })
        .eq("id", queueId);
      if (queueCompleteError) throw new Error("DB_COMPLETE_QUEUE");
    }

    return response({
      ok: true,
      edge_region: Deno.env.get("SB_REGION") ?? null,
      symbol,
      run_id: runId,
      queue_id: queueId || null,
      counts: {
        candles: candles.length,
        premium_index: premiumIndex.length,
        funding: funding.length,
        open_interest: oi.length,
        account_ratio: ratios.length,
        binance_global_ratio_5m: binanceGlobalRatio.length,
      },
      quality,
      live_trading: false,
      private_bybit_api: false,
    });
  } catch (error) {
    const code = error instanceof Error ? error.message.slice(0, 120) : "UNKNOWN_ERROR";
    if (runId) {
      await supabase
        .from("derivatives_collection_runs")
        .update({
          completed_at: new Date().toISOString(),
          status: "failed",
          error_code: code,
        })
        .eq("id", runId);
    }
    if (queueId) {
      await supabase
        .from("derivatives_backfill_queue")
        .update({
          status: "failed",
          collection_run_id: runId,
          last_error: code,
          updated_at: new Date().toISOString(),
        })
        .eq("id", queueId);
    }
    return response({
      ok: false,
      edge_region: Deno.env.get("SB_REGION") ?? null,
      error: code,
      live_trading: false,
    }, 502);
  }
});
