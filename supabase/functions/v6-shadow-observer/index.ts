import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SECRET_KEYS = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
const SECRET_KEY = SECRET_KEYS["default"] || Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const supabase = createClient(SUPABASE_URL, SECRET_KEY, {
  auth: { persistSession: false, autoRefreshToken: false },
});

const SIGNAL_MAX_AGE_MS = 14 * 60 * 1000;
const MAX_MICROSTRUCTURE_AGE_MS = 2_000;
const ADVERSE_IMBALANCE = 0.35;
const MIN_BLACKLIST_SAMPLES = 8;
const HARD_LOSING_STREAK = 4;
const HARD_MEAN_NET_RETURN = -0.003;
const WARMUP_PER_ASSET = 20;

type SymbolName = "BTC/USDT" | "ETH/USDT";

const rawSymbol = (symbol: SymbolName) =>
  symbol === "BTC/USDT" ? "BTCUSDT" : "ETHUSDT";

function n(value: unknown): number {
  const result = Number(value);
  if (!Number.isFinite(result)) throw new Error("non_finite_numeric_value");
  return result;
}

function sampleStd(values: number[]): number {
  if (values.length < 2) return 0;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((sum, x) => sum + (x - mean) ** 2, 0) /
    (values.length - 1);
  return Math.sqrt(Math.max(variance, 0));
}

function ewm(values: number[], alpha: number): number[] {
  const out: number[] = [];
  let current: number | null = null;
  for (const value of values) {
    if (!Number.isFinite(value)) {
      out.push(current ?? Number.NaN);
      continue;
    }
    current = current == null ? value : alpha * value + (1 - alpha) * current;
    out.push(current);
  }
  return out;
}

function computeFeatures(candles: any[]) {
  if (candles.length < 60) throw new Error("insufficient_closed_15m_candles");
  const closes = candles.map((x) => n(x.close_price));
  const highs = candles.map((x) => n(x.high_price));
  const lows = candles.map((x) => n(x.low_price));

  const tr: number[] = [];
  for (let i = 0; i < candles.length; i++) {
    const range = highs[i] - lows[i];
    if (i === 0) {
      tr.push(range);
    } else {
      tr.push(Math.max(
        range,
        Math.abs(highs[i] - closes[i - 1]),
        Math.abs(lows[i] - closes[i - 1]),
      ));
    }
  }
  const atrSeries = ewm(tr, 1 / 14);
  const atr14 = atrSeries[atrSeries.length - 1];

  const returns: number[] = [];
  for (let i = 1; i < closes.length; i++) {
    returns.push(closes[i] / closes[i - 1] - 1);
  }
  const recentReturns = returns.slice(-20);
  if (recentReturns.length < 20) throw new Error("insufficient_returns");
  const realizedVol20 = sampleStd(recentReturns) * Math.sqrt(20);
  const realizedAnnualized = realizedVol20 * Math.sqrt((365 * 24 * 4) / 20);

  const ema20 = ewm(closes, 2 / 21).at(-1)!;
  const ema50 = ewm(closes, 2 / 51).at(-1)!;

  return {
    close: closes.at(-1)!,
    atr14,
    realizedAnnualized: Math.max(0, realizedAnnualized),
    trend: ema20 > ema50 ? "up" : "down",
  };
}

function rollingStats(symbol: SymbolName, rows: any[]) {
  const selected = rows.filter((r) => r.symbol === symbol).slice(-40);
  const returns = selected.map((r) => n(r.net_return));
  let equity = 1;
  let peak = 1;
  let maxDrawdown = 0;
  let losingStreak = 0;
  let currentStreak = 0;
  for (const value of returns) {
    equity *= 1 + value;
    peak = Math.max(peak, equity);
    if (peak > 0) maxDrawdown = Math.max(maxDrawdown, (peak - equity) / peak);
    if (value < 0) {
      currentStreak += 1;
      losingStreak = Math.max(losingStreak, currentStreak);
    } else {
      currentStreak = 0;
    }
  }
  const compounded = returns.reduce((eq, r) => eq * (1 + r), 1) - 1;
  const hits = returns.filter((r) => r > 0).length;
  return {
    symbol,
    sample_size: returns.length,
    hit_rate: returns.length ? hits / returns.length : 0,
    mean_net_return: returns.length
      ? returns.reduce((a, b) => a + b, 0) / returns.length
      : 0,
    cumulative_net_return: compounded,
    max_drawdown: maxDrawdown,
    losing_streak: losingStreak,
  };
}

function buildPortfolioState(rows: any[]) {
  const recent = rows.slice(-80);
  const btc = rollingStats("BTC/USDT", recent);
  const eth = rollingStats("ETH/USDT", recent);
  const portfolioReturns = recent.slice(-40).map((r) => n(r.net_return));
  let equity = 1;
  let peak = 1;
  let portfolioDrawdown = 0;
  for (const value of portfolioReturns) {
    equity *= 1 + value;
    peak = Math.max(peak, equity);
    if (peak > 0) {
      portfolioDrawdown = Math.max(portfolioDrawdown, (peak - equity) / peak);
    }
  }
  return {
    as_of: new Date().toISOString(),
    assets: { "BTC/USDT": btc, "ETH/USDT": eth },
    portfolio_drawdown: portfolioDrawdown,
    recent_trade_count: btc.sample_size + eth.sample_size,
    warmup_complete:
      btc.sample_size >= WARMUP_PER_ASSET && eth.sample_size >= WARMUP_PER_ASSET,
    source: "immutable_reward_memory",
  };
}

function regimeKey(
  trend: string,
  realizedVolatility: number,
  impliedVolatility: number | null,
): string {
  let vol = "unknown_iv";
  if (
    impliedVolatility != null &&
    Number.isFinite(impliedVolatility) &&
    realizedVolatility > 0
  ) {
    const ratio = impliedVolatility / realizedVolatility;
    vol = ratio >= 1.25 ? "high_iv" : ratio <= 0.80 ? "low_iv" : "normal_iv";
  }
  return `${trend}|${vol}`;
}

function portfolioAction(
  state: any,
  symbol: SymbolName,
  currentRegime: string,
  adaptivePolicy: any | null,
) {
  const blocked: SymbolName[] = [];
  for (const symbol of ["BTC/USDT", "ETH/USDT"] as SymbolName[]) {
    const stats = state.assets[symbol];
    if (stats.sample_size < MIN_BLACKLIST_SAMPLES) continue;
    if (
      stats.losing_streak >= HARD_LOSING_STREAK ||
      stats.mean_net_return <= HARD_MEAN_NET_RETURN
    ) blocked.push(symbol);
  }

  const weights: Record<SymbolName, number> = {
    "BTC/USDT": blocked.includes("BTC/USDT") ? 0 : 1,
    "ETH/USDT": blocked.includes("ETH/USDT") ? 0 : 1,
  };

  let adaptiveVersion: string | null = null;
  let appliedWeight = weights[symbol];
  let targetWeight = weights[symbol];
  let evidenceCount = 0;
  let evidenceSource = "none";
  let opportunityCostBoost = false;
  let lossGuard = false;

  if (adaptivePolicy) {
    const cap = Math.max(0, Math.min(1, n(adaptivePolicy.applied_weight)));
    targetWeight = Math.max(0, Math.min(1, n(adaptivePolicy.target_weight)));
    appliedWeight = Math.min(weights[symbol], cap);
    weights[symbol] = appliedWeight;
    adaptiveVersion = String(adaptivePolicy.version);
    evidenceCount = Number(adaptivePolicy.evidence_count || 0);
    evidenceSource = String(adaptivePolicy.evidence_source || "unknown");
    opportunityCostBoost = Boolean(adaptivePolicy.opportunity_cost_boost);
    lossGuard = Boolean(adaptivePolicy.loss_guard);
    if (appliedWeight === 0 && !blocked.includes(symbol)) blocked.push(symbol);
  }

  return {
    asset_weights: weights,
    blacklisted_assets: blocked,
    policy_version: "v6-shadow-adaptive-v2",
    reason: adaptivePolicy
      ? "rolling_regime_aware_gradual_weight_shadow_only"
      : "adaptive_v2_policy_missing_fail_safe",
    shadow_mode: true,
    policy_log_prob: null,
    policy_action: null,
    adaptive_policy_version: adaptiveVersion,
    regime_key: currentRegime,
    target_weight: targetWeight,
    applied_weight: appliedWeight,
    evidence_count: evidenceCount,
    evidence_source: evidenceSource,
    opportunity_cost_boost: opportunityCostBoost,
    loss_guard: lossGuard,
    maximum_relative_risk: 1,
    v51_protocol_changed: false,
    trading_authority: false,
  };
}
function dynamicRiskAction(state: any) {
  let stop = 1.5;
  let rr = 1.0;
  let trailing = 0.0;
  let forceNoTrade = false;
  const reasons = ["baseline_before_neural_policy"];

  if (
    state.microstructure_age_ms == null ||
    state.microstructure_age_ms > MAX_MICROSTRUCTURE_AGE_MS
  ) reasons.push("microstructure_stale_shadow");

  if (state.implied_volatility == null) {
    reasons.push("implied_volatility_missing_shadow");
  } else if (state.realized_volatility > 0) {
    const ratio = state.implied_volatility / state.realized_volatility;
    if (ratio >= 1.25) {
      stop = Math.min(2.25, stop * Math.min(1.25, 1 + (ratio - 1) * 0.15));
      rr = Math.max(0.75, rr * 0.90);
      trailing = Math.min(0.90, Math.max(trailing, 0.35));
      reasons.push("volatility_shock_widen_stop_tighten_profit");
    }
  }

  const bybit = state.bybit_orderbook_imbalance;
  const binance = state.binance_orderbook_imbalance;
  if (bybit != null && binance != null) {
    if (
      state.side === "LONG" &&
      bybit <= -ADVERSE_IMBALANCE &&
      binance <= -ADVERSE_IMBALANCE
    ) {
      forceNoTrade = true;
      reasons.push("cross_venue_orderbook_veto_long");
    }
    if (
      state.side === "SHORT" &&
      bybit >= ADVERSE_IMBALANCE &&
      binance >= ADVERSE_IMBALANCE
    ) {
      forceNoTrade = true;
      reasons.push("cross_venue_orderbook_veto_short");
    }
  }

  return {
    stop_atr_multiplier: Math.min(2.25, Math.max(0.75, stop)),
    reward_to_risk_multiplier: Math.min(1.50, Math.max(0.75, rr)),
    trailing_lock_fraction: Math.min(0.90, Math.max(0, trailing)),
    force_no_trade: forceNoTrade,
    policy_version: "v6-symbolic-baseline",
    reason: reasons.join(";"),
    shadow_mode: true,
    policy_log_prob: null,
    policy_action: null,
  };
}

async function observeSymbol(
  symbol: SymbolName,
  portfolioState: any,
  adaptivePolicies: Record<string, any>,
) {
  const { data: signals, error: signalError } = await supabase
    .from("strategy_signal_snapshots")
    .select("id,experiment_id,market_snapshot_id,strategy_version,symbol,observed_at,context_timestamp,signal,payload")
    .eq("strategy_version", "V5.1")
    .eq("symbol", symbol)
    .order("observed_at", { ascending: false })
    .limit(1);
  if (signalError) throw signalError;
  const signal = signals?.[0];
  if (!signal) return { symbol, status: "SKIPPED", reason: "no_v51_signal" };

  const signalAge = Date.now() - new Date(signal.observed_at).getTime();
  if (signalAge > SIGNAL_MAX_AGE_MS) {
    return { symbol, status: "SKIPPED", reason: "stale_v51_signal", signal_age_ms: signalAge };
  }
  if (!["LONG", "SHORT"].includes(signal.signal)) {
    return { symbol, status: "SKIPPED", reason: "v51_no_trade", signal: signal.signal };
  }

  const closedBefore = new Date(Date.now() - 15 * 60 * 1000).toISOString();
  const { data: rawCandles, error: candlesError } = await supabase
    .from("market_candles_15m")
    .select("open_time,open_price,high_price,low_price,close_price,volume")
    .eq("symbol", rawSymbol(symbol))
    .lte("open_time", closedBefore)
    .order("open_time", { ascending: false })
    .limit(200);
  if (candlesError) throw candlesError;
  const candles = [...(rawCandles || [])].reverse();
  const features = computeFeatures(candles);

  const { data: microRows, error: microError } = await supabase
    .from("v6_microstructure_latest")
    .select("venue,captured_at,imbalance,spread_bps")
    .eq("symbol", symbol);
  if (microError) throw microError;
  const bybit = (microRows || []).find((x) => x.venue === "bybit");
  const binance = (microRows || []).find((x) => x.venue === "binance");

  let microAge: number | null = null;
  if (bybit && binance) {
    const oldest = Math.min(
      new Date(bybit.captured_at).getTime(),
      new Date(binance.captured_at).getTime(),
    );
    microAge = Math.max(0, Date.now() - oldest);
  }

  const { data: ivRows, error: ivError } = await supabase
    .from("v6_volatility_snapshots")
    .select("source,captured_at,mark_iv,expiry,sample_count,payload")
    .eq("symbol", symbol)
    .order("captured_at", { ascending: false })
    .limit(1);
  if (ivError) throw ivError;
  const iv = ivRows?.[0] || null;

  const volatilityState = {
    symbol,
    side: signal.signal,
    entry_price: features.close,
    atr_14: features.atr14,
    realized_volatility: features.realizedAnnualized,
    implied_volatility: iv ? n(iv.mark_iv) : null,
    bybit_orderbook_imbalance: bybit ? n(bybit.imbalance) : null,
    binance_orderbook_imbalance: binance ? n(binance.imbalance) : null,
    spread_bps: bybit ? n(bybit.spread_bps) : 0,
    market_regime: `${features.trend}|edge_shadow_observer`,
    microstructure_age_ms: microAge,
  };

  const currentRegime = regimeKey(
    features.trend,
    features.realizedAnnualized,
    iv ? n(iv.mark_iv) : null,
  );
  const adaptivePolicy =
    adaptivePolicies[`${symbol}|${currentRegime}`] ||
    adaptivePolicies[`${symbol}|all`] ||
    null;
  const pAction = portfolioAction(
    portfolioState,
    symbol,
    currentRegime,
    adaptivePolicy,
  );
  const rAction = dynamicRiskAction(volatilityState);
  const stateJson = {
    portfolio_state: portfolioState,
    volatility_state: volatilityState,
    iv_snapshot: iv,
    observer: {
      kind: "supabase_edge_shadow_observer_v2",
      trading_authority: false,
      v51_protocol_mutation: false,
      llm_invoked: false,
      deterministic_risk_engine_bypassed_for_trading: false,
      purpose: "shadow_action_collection_only",
    },
  };

  const { data: existing, error: existingError } = await supabase
    .from("marl_action_logs")
    .select("agent_role")
    .eq("signal_snapshot_id", signal.id);
  if (existingError) throw existingError;
  const roles = new Set((existing || []).map((x) => x.agent_role));

  const rows = [];
  if (!roles.has("portfolio")) {
    rows.push({
      experiment_id: signal.experiment_id,
      market_snapshot_id: signal.market_snapshot_id,
      signal_snapshot_id: signal.id,
      agent_role: "portfolio",
      policy_version: pAction.policy_version,
      state_json: stateJson,
      action_json: pAction,
      projected_action_json: pAction,
      shadow_mode: true,
      vetoed_by_risk: false,
    });
  }
  if (!roles.has("dynamic_risk")) {
    rows.push({
      experiment_id: signal.experiment_id,
      market_snapshot_id: signal.market_snapshot_id,
      signal_snapshot_id: signal.id,
      agent_role: "dynamic_risk",
      policy_version: rAction.policy_version,
      state_json: stateJson,
      action_json: rAction,
      projected_action_json: rAction,
      shadow_mode: true,
      vetoed_by_risk: false,
    });
  }

  if (rows.length) {
    const { error: insertError } = await supabase.from("marl_action_logs").insert(rows);
    if (insertError) throw insertError;
  }

  return {
    symbol,
    status: "OBSERVED",
    signal: signal.signal,
    signal_snapshot_id: signal.id,
    actions_inserted: rows.length,
    portfolio_policy: pAction.policy_version,
    dynamic_policy: rAction.policy_version,
    dynamic_force_no_trade_shadow: rAction.force_no_trade,
    microstructure_age_ms: microAge,
    regime_key: currentRegime,
    adaptive_policy_version: pAction.adaptive_policy_version,
    adaptive_applied_weight: pAction.applied_weight,
  };
}

Deno.serve(async (req: Request) => {
  const token = req.headers.get("x-v6-observer-token") || "";
  const { data: valid, error: authError } = await supabase.rpc(
    "v6_validate_shadow_observer_token",
    { p_token: token },
  );
  if (authError || valid !== true) {
    return Response.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }

  const { data: activePolicy, error: policyError } = await supabase
    .from("marl_policy_versions")
    .select("id,version,status")
    .eq("agent_role", "mappo_shared")
    .eq("status", "active")
    .limit(1);
  if (policyError) {
    return Response.json({ ok: false, error: "policy_read_failed" }, { status: 503 });
  }
  if (activePolicy?.length) {
    return Response.json({
      ok: true,
      status: "SKIPPED",
      reason: "active_mappo_policy_requires_python_torch_runtime",
      trading_authority: false,
    });
  }

  const { data: rewards, error: rewardsError } = await supabase
    .from("marl_reward_memory")
    .select("symbol,net_return,max_drawdown,created_at")
    .order("created_at", { ascending: false })
    .limit(80);
  if (rewardsError) {
    return Response.json({ ok: false, error: "reward_memory_read_failed" }, { status: 503 });
  }
  const portfolioState = buildPortfolioState([...(rewards || [])].reverse());

  const { data: policyRows, error: shadowPolicyError } = await supabase
    .from("v6_shadow_regime_policy_versions")
    .select(
      "symbol,regime_key,version,sample_count,evidence_count,evidence_source,target_weight,applied_weight,opportunity_cost_boost,loss_guard,created_at",
    )
    .order("sample_count", { ascending: false })
    .order("created_at", { ascending: false })
    .limit(100);
  if (shadowPolicyError) {
    return Response.json(
      { ok: false, error: "adaptive_v2_policy_read_failed" },
      { status: 503 },
    );
  }

  const adaptivePolicies: Record<string, any> = {};
  for (const row of policyRows || []) {
    const key = `${row.symbol}|${row.regime_key}`;
    if (adaptivePolicies[key] == null) adaptivePolicies[key] = row;
  }

  const results = await Promise.allSettled([
    observeSymbol("BTC/USDT", portfolioState, adaptivePolicies),
    observeSymbol("ETH/USDT", portfolioState, adaptivePolicies),
  ]);

  const normalized = results.map((result, index) =>
    result.status === "fulfilled"
      ? result.value
      : {
          symbol: index === 0 ? "BTC/USDT" : "ETH/USDT",
          status: "FAILED",
          error: String(result.reason),
        }
  );

  return Response.json({
    ok: normalized.every((x) => x.status !== "FAILED"),
    mode: "V6_SHADOW_ACTION_OBSERVER_ADAPTIVE_V2",
    trading_authority: false,
    v51_protocol_changed: false,
    llm_invoked: false,
    results: normalized,
  });
});
