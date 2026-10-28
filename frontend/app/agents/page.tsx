import {getOperationsSnapshot} from "@/lib/operations";

export const dynamic = "force-dynamic";

const agentDefinitions = [
  ["01", "Market Intelligence", "Regime · structure · momentum · volatility · liquidity"],
  ["02", "Trade Strategist", "LONG / SHORT / WAIT using deterministic price levels only"],
  ["03", "Risk & Devil's Advocate", "Attempts to veto every weak or conflicting setup"],
] as const;

export default async function Agents() {
  const snapshot = await getOperationsSnapshot(80);
  const runtime = snapshot?.orchestration?.capability;
  const attempts = snapshot?.orchestration?.attempts ?? [];
  const latestAttempt = attempts[0];
  const latestSignal = snapshot?.latest_signals?.find((s) => s.signal !== "NO_TRADE");

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <p className="eyebrow">LANGGRAPH ORCHESTRATION</p>
          <h1>Agents</h1>
          <p className="muted">Real runtime state, not simulated UI status.</p>
        </div>
        <div className="statusPill">
          <span className="pulseDot" />
          {runtime?.enabled ? "RUNTIME READY" : "FAIL-CLOSED"}
        </div>
      </header>

      {!runtime?.enabled && (
        <section className="notice">
          External LLM runtime is not configured. Prospective LONG/SHORT candidates are recorded,
          but the system blocks agent execution and defaults to NO_TRADE.
        </section>
      )}

      {latestSignal && (
        <section className="candidateBanner">
          <div>
            <span className="label">LATEST PROSPECTIVE CANDIDATE</span>
            <strong>{latestSignal.symbol} · {latestSignal.signal}</strong>
            <small>Feature {Number(latestSignal.feature_value).toFixed(5)} · context {new Date(latestSignal.context_timestamp).toLocaleString()}</small>
          </div>
          <div className="candidateState">
            <span>Orchestrator</span>
            <strong>{latestAttempt?.status ?? "WAITING"}</strong>
            <small>{latestAttempt?.reason ?? "Awaiting orchestration preflight"}</small>
          </div>
        </section>
      )}

      <section className="agentGrid">
        {agentDefinitions.map(([id, name, desc], index) => {
          const recorded = snapshot?.agents?.[index] ?? null;
          const runtimeText = recorded
            ? (recorded.provider ?? "provider") + " · " + (recorded.model_id ?? "model") + " · " + (recorded.latency_ms ?? "—") + " ms"
            : latestAttempt?.reason ?? "No agent run recorded";

          return (
            <article className="panel agentCard" key={id}>
              <span className="agentId">{id}</span>
              <h2>{name}</h2>
              <p>{desc}</p>
              <div className="agentRuntimeState">
                <span className="label">LIVE STATE</span>
                <strong>{recorded?.decision ?? (latestAttempt?.status === "BLOCKED" ? "BLOCKED" : "IDLE")}</strong>
                <small>{runtimeText}</small>
              </div>
              <div className="agentRule">Failure → NO TRADE</div>
            </article>
          );
        })}
      </section>
    </div>
  );
}
