export type ForwardState = {
  health_status: string;
  health_checked_at?: string;
  btc_signal: string;
  eth_signal: string;
  btc_trade_count: number;
  eth_trade_count: number;
  btc_mean_net_return: number | null;
  eth_mean_net_return: number | null;
  btc_profit_factor: number | null;
  eth_profit_factor: number | null;
  btc_gate: string;
  eth_gate: string;
  agent_runs: number;
  consensus_decisions: number;
  risk_evaluations: number;
  paper_trades: number;
};

export type Summary = {
  version: string;
  paper_balance: number;
  equity: number;
  today_pnl: number;
  open_positions: number;
  closed_trades: number;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  net_pnl: number;
  profit_factor: number | null;
  expectancy: number;
  average_win: number;
  average_loss: number;
  max_drawdown: number;
  sharpe: number | null;
  sortino: number | null;
  max_losing_streak: number;
  experiment_status?: string;
  forward?: ForwardState;
};

export type Candle = {
  start_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type MarketSnapshot = {
  ticker: {
    symbol: string;
    last_price: number;
    funding_rate: number;
    open_interest: number;
  };
  latest_ratio: number;
  candles: Record<string, Candle[]>;
  market_data_age_ms: number;
};

type SignalRow = {
  symbol: string;
  signal: string;
  context_timestamp?: string;
  feature_value?: number;
};

type RuleRow = {
  symbol: string;
  evaluated_trade_count?: number;
  mean_net_return?: number;
  profit_factor?: number;
};

type GateRow = {
  symbol: string;
  qualification_state?: string;
};

type DashboardSnapshot = {
  experiment?: {
    version?: string;
    status?: string;
    execution_mode?: string;
  };
  latest_signals?: SignalRow[];
  reconstructed?: RuleRow[];
  gate?: GateRow[];
  health?: {
    status?: string;
    checked_at?: string;
  };
  execution_counts?: {
    agent_runs?: number;
    consensus_decisions?: number;
    risk_evaluations?: number;
    paper_trades?: number;
    trade_events?: number;
  };
  paper_trades?: Record<string, unknown>[];
  markets?: Record<string, MarketSnapshot>;
};

async function getDashboardSnapshot(): Promise<DashboardSnapshot | null> {
  const base = process.env.TRICONSENSUS_SUPABASE_URL;
  const publishable = process.env.TRICONSENSUS_SUPABASE_PUBLISHABLE_KEY;
  const token = process.env.TRICONSENSUS_DASHBOARD_TOKEN;

  if (!base || !publishable || !token) return null;

  try {
    const response = await fetch(
      base.replace(/\/$/, "") + "/rest/v1/rpc/triconsensus_dashboard_snapshot",
      {
        method: "POST",
        headers: {
          apikey: publishable,
          Authorization: "Bearer " + publishable,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({p_token: token}),
        cache: "no-store",
      },
    );

    if (!response.ok) return null;
    return (await response.json()) as DashboardSnapshot;
  } catch {
    return null;
  }
}

function asNumber(value: unknown): number {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

function maxLosingStreak(pnls: number[]): number {
  let current = 0;
  let max = 0;
  for (const pnl of pnls) {
    if (pnl < 0) {
      current += 1;
      max = Math.max(max, current);
    } else {
      current = 0;
    }
  }
  return max;
}

export async function getSummary(): Promise<Summary | null> {
  const snapshot = await getDashboardSnapshot();
  if (!snapshot) return null;

  const trades = snapshot.paper_trades ?? [];
  const closed = trades.filter((t) => String(t.status ?? "") === "closed");
  const open = trades.filter((t) => String(t.status ?? "") === "open");
  const pnls = closed.map((t) => asNumber(t.pnl_net));
  const wins = pnls.filter((p) => p > 0);
  const losses = pnls.filter((p) => p < 0);
  const netPnl = pnls.reduce((a, b) => a + b, 0);
  const grossWin = wins.reduce((a, b) => a + b, 0);
  const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));
  const startingBalance = 10000;

  const signals = snapshot.latest_signals ?? [];
  const rules = snapshot.reconstructed ?? [];
  const gates = snapshot.gate ?? [];
  const counts = snapshot.execution_counts ?? {};
  const btcRule = rules.find((r) => r.symbol === "BTC/USDT");
  const ethRule = rules.find((r) => r.symbol === "ETH/USDT");
  const btcSignal = signals.find((r) => r.symbol === "BTC/USDT");
  const ethSignal = signals.find((r) => r.symbol === "ETH/USDT");
  const btcGate = gates.find((r) => r.symbol === "BTC/USDT");
  const ethGate = gates.find((r) => r.symbol === "ETH/USDT");

  return {
    version: snapshot.experiment?.version ?? "V5.1-PAPER",
    experiment_status: snapshot.experiment?.status ?? "forward_test",
    paper_balance: startingBalance + netPnl,
    equity: startingBalance + netPnl,
    today_pnl: 0,
    open_positions: open.length,
    closed_trades: closed.length,
    trades: closed.length,
    wins: wins.length,
    losses: losses.length,
    win_rate: closed.length ? wins.length / closed.length : 0,
    net_pnl: netPnl,
    profit_factor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : null,
    expectancy: closed.length ? netPnl / closed.length : 0,
    average_win: wins.length ? grossWin / wins.length : 0,
    average_loss: losses.length ? -grossLoss / losses.length : 0,
    max_drawdown: 0,
    sharpe: null,
    sortino: null,
    max_losing_streak: maxLosingStreak(pnls),
    forward: {
      health_status: snapshot.health?.status ?? "unknown",
      health_checked_at: snapshot.health?.checked_at,
      btc_signal: btcSignal?.signal ?? "UNKNOWN",
      eth_signal: ethSignal?.signal ?? "UNKNOWN",
      btc_trade_count: btcRule?.evaluated_trade_count ?? 0,
      eth_trade_count: ethRule?.evaluated_trade_count ?? 0,
      btc_mean_net_return: btcRule?.mean_net_return ?? null,
      eth_mean_net_return: ethRule?.mean_net_return ?? null,
      btc_profit_factor: btcRule?.profit_factor ?? null,
      eth_profit_factor: ethRule?.profit_factor ?? null,
      btc_gate: btcGate?.qualification_state ?? "UNKNOWN",
      eth_gate: ethGate?.qualification_state ?? "UNKNOWN",
      agent_runs: counts.agent_runs ?? 0,
      consensus_decisions: counts.consensus_decisions ?? 0,
      risk_evaluations: counts.risk_evaluations ?? 0,
      paper_trades: counts.paper_trades ?? 0,
    },
  };
}

export async function getMarket(
  symbol: "BTCUSDT" | "ETHUSDT",
): Promise<MarketSnapshot | null> {
  const snapshot = await getDashboardSnapshot();
  return snapshot?.markets?.[symbol] ?? null;
}

export async function getTrades() {
  const snapshot = await getDashboardSnapshot();
  if (!snapshot) return null;
  return {
    version: snapshot.experiment?.version ?? "V5.1-PAPER",
    trades: snapshot.paper_trades ?? [],
  };
}

export async function getReplay(id: string): Promise<Record<string, unknown> | null> {
  const base = process.env.TRICONSENSUS_SUPABASE_URL;
  const publishable = process.env.TRICONSENSUS_SUPABASE_PUBLISHABLE_KEY;
  const token = process.env.TRICONSENSUS_DASHBOARD_TOKEN;
  if (!base || !publishable || !token) return null;

  try {
    const response = await fetch(
      base.replace(/\/$/, "") + "/rest/v1/rpc/triconsensus_replay",
      {
        method: "POST",
        headers: {
          apikey: publishable,
          Authorization: "Bearer " + publishable,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({p_token: token, p_consensus_id: id}),
        cache: "no-store",
      },
    );
    if (!response.ok) return null;
    const replay = (await response.json()) as Record<string, unknown>;
    return Object.keys(replay).length ? replay : null;
  } catch {
    return null;
  }
}
