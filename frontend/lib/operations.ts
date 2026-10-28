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

export type OrchestrationAttempt = {
  id: string;
  signal_snapshot_id: string;
  symbol: string;
  signal: string;
  context_timestamp: string;
  status: string;
  reason: string;
  payload: Record<string, unknown>;
  created_at: string;
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
      unrealized_pnl: number;
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
  orchestration?: {
    capability: {
      capability?: string;
      enabled?: boolean;
      reason?: string;
      updated_at?: string;
    };
    attempts: OrchestrationAttempt[];
  };
};

async function rpc(
  base: string,
  publishable: string,
  name: string,
  payload: Record<string, unknown>,
) {
  return fetch(base.replace(/\/$/, "") + "/rest/v1/rpc/" + name, {
    method: "POST",
    headers: {
      apikey: publishable,
      Authorization: "Bearer " + publishable,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
}

export async function getOperationsSnapshot(limit = 100): Promise<OperationsSnapshot | null> {
  const base = process.env.TRICONSENSUS_SUPABASE_URL;
  const publishable = process.env.TRICONSENSUS_SUPABASE_PUBLISHABLE_KEY;
  const token = process.env.TRICONSENSUS_DASHBOARD_TOKEN;
  if (!base || !publishable || !token) return null;

  try {
    const [liveResponse, orchestrationResponse] = await Promise.all([
      rpc(base, publishable, "triconsensus_live_snapshot", {
        p_token: token,
        p_limit: Math.max(10, Math.min(limit, 200)),
      }),
      rpc(base, publishable, "triconsensus_orchestration_status", {
        p_token: token,
      }),
    ]);

    if (!liveResponse.ok) return null;
    const snapshot = (await liveResponse.json()) as OperationsSnapshot;

    if (orchestrationResponse.ok) {
      const orchestration = (await orchestrationResponse.json()) as OperationsSnapshot["orchestration"];
      snapshot.orchestration = orchestration;
      const orchestrationEvents: OpsEvent[] = (orchestration?.attempts ?? []).map((attempt) => ({
        event_time: attempt.created_at,
        kind: "ORCHESTRATOR",
        subject: attempt.symbol,
        state: attempt.status,
        title: attempt.reason,
        details: {
          signal: attempt.signal,
          context_timestamp: attempt.context_timestamp,
          ...attempt.payload,
        },
      }));
      snapshot.events = [...orchestrationEvents, ...(snapshot.events ?? [])]
        .sort((a, b) => Date.parse(b.event_time) - Date.parse(a.event_time))
        .slice(0, Math.max(10, Math.min(limit, 200)));
    }

    return snapshot;
  } catch {
    return null;
  }
}
