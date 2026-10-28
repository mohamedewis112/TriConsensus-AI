export type OpsEvent = {
  event_time: string;
  kind: string;
  subject: string;
  state: string;
  title: string;
  details: Record<string, unknown>;
};

export type AgentState = {
  agent_role: string;
  decision: string | null;
  confidence: number | null;
  provider: string | null;
  model_id: string | null;
  is_valid: boolean | null;
  error_code: string | null;
  latency_ms: number | null;
  created_at: string;
};

export type ResearchOutcome = {
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  gross_return: number;
  net_return: number;
  hit: boolean;
  entry_context_timestamp: string;
  exit_candle_open_time: string;
  evaluated_at: string;
};

export type OperationsSnapshot = {
  generated_at: string;
  health: {status?: string; checked_at?: string; checks?: Record<string, unknown>};
  latest_signals: Array<{
    symbol: string;
    signal: string;
    context_timestamp: string;
    feature_value: number;
    low_threshold: number;
    high_threshold: number;
    reason: string;
    created_at: string;
  }>;
  finance: {
    paper: {
      starting_balance: number;
      realized_pnl: number;
      fees: number;
      open_positions: number;
      closed_trades: number;
      wins: number;
      losses: number;
      gross_profit: number;
      gross_loss: number;
      notional_open: number;
    };
    research_outcomes: ResearchOutcome[];
  };
  events: OpsEvent[];
  agents: AgentState[];
  latest_consensus: Record<string, unknown>;
  latest_risk: Record<string, unknown>;
  open_trades: Record<string, unknown>[];
};

export async function getOperationsSnapshot(limit = 100): Promise<OperationsSnapshot | null> {
  const base = process.env.TRICONSENSUS_SUPABASE_URL;
  const publishable = process.env.TRICONSENSUS_SUPABASE_PUBLISHABLE_KEY;
  const token = process.env.TRICONSENSUS_DASHBOARD_TOKEN;
  if (!base || !publishable || !token) return null;

  try {
    const response = await fetch(
      base.replace(/\/$/, "") + "/rest/v1/rpc/triconsensus_live_snapshot",
      {
        method: "POST",
        headers: {
          apikey: publishable,
          Authorization: "Bearer " + publishable,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({p_token: token, p_limit: Math.max(10, Math.min(limit, 200))}),
        cache: "no-store",
      },
    );
    if (!response.ok) return null;
    return (await response.json()) as OperationsSnapshot;
  } catch {
    return null;
  }
}
