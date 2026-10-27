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
    bid: number;
    ask: number;
    funding_rate: number;
    open_interest: number;
  };
  orderbook: {
    imbalance: number;
    spread_bps: number;
    bid_depth: number;
    ask_depth: number;
  };
  candles: Record<string, Candle[]>;
  market_data_age_ms: number;
};

async function api<T>(path: string): Promise<T | null> {
  const base = process.env.TRICONSENSUS_API_URL;
  const key = process.env.TRICONSENSUS_API_KEY;
  if (!base || !key) return null;

  try {
    const response = await fetch(base.replace(/\/$/, "") + path, {
      headers: {"X-TriConsensus-Key": key},
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export const getSummary = () => api<Summary>("/v1/analytics/summary?version=V1");
export const getMarket = (symbol: "BTCUSDT" | "ETHUSDT") =>
  api<MarketSnapshot>("/v1/market/" + symbol);
export const getTrades = () =>
  api<{version: string; trades: Record<string, unknown>[]}>("/v1/trades?version=V1&limit=100");
export const getReplay = (id: string) =>
  api<Record<string, unknown>>("/v1/replay/" + encodeURIComponent(id));
