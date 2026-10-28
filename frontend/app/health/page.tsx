import {getHealthSnapshot} from "@/lib/operations";

export const dynamic = "force-dynamic";

function ageLabel(ts?: string) {
  if (!ts) return "—";
  const ms = Date.now() - Date.parse(ts);
  if (!Number.isFinite(ms)) return "—";
  const sec = Math.max(0, Math.floor(ms / 1000));
  if (sec < 60) return sec + "s";
  const min = Math.floor(sec / 60);
  if (min < 60) return min + "m";
  return Math.floor(min / 60) + "h";
}

export default async function HealthPage() {
  const health = await getHealthSnapshot();
  const jobs = health?.cron_jobs ?? [];
  const capabilities = health?.runtime_capabilities ?? [];

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <p className="eyebrow">SYSTEM HEALTH · FAIL-CLOSED</p>
          <h1>Health Center</h1>
          <p className="muted">Collectors, forward pipeline, orchestration runtime and execution telemetry.</p>
        </div>
        <div className="statusPill">
          <span className="pulseDot" />
          {health?.watchdog?.status?.toUpperCase() ?? "UNKNOWN"}
        </div>
      </header>

      {!health && <section className="notice">Health backend unavailable. Default action remains NO_TRADE.</section>}

      {health && (
        <>
          <section className="metrics">
            <article className="metricCard"><span>Watchdog</span><strong>{health.watchdog?.status?.toUpperCase() ?? "UNKNOWN"}</strong></article>
            <article className="metricCard"><span>BTC Candle Age</span><strong>{ageLabel(health.market_freshness?.BTCUSDT?.latest_candle)}</strong></article>
            <article className="metricCard"><span>ETH Candle Age</span><strong>{ageLabel(health.market_freshness?.ETHUSDT?.latest_candle)}</strong></article>
            <article className="metricCard"><span>Agent Runs</span><strong>{health.execution_counts.agent_runs}</strong></article>
            <article className="metricCard"><span>Consensus</span><strong>{health.execution_counts.consensus_decisions}</strong></article>
            <article className="metricCard"><span>Paper Trades</span><strong>{health.execution_counts.paper_trades}</strong></article>
          </section>

          <section className="twoCol">
            <article className="panel">
              <span className="label">RUNTIME CAPABILITIES</span>
              <div className="agentStateList">
                {capabilities.map((cap) => (
                  <div key={cap.capability}>
                    <span>{cap.capability}</span>
                    <strong className={cap.enabled ? "stateGood" : "stateBad"}>{cap.enabled ? "ENABLED" : "DISABLED"}</strong>
                    <small>{cap.reason ?? "—"}</small>
                  </div>
                ))}
              </div>
            </article>

            <article className="panel">
              <span className="label">MARKET FRESHNESS</span>
              <div className="signalStateList">
                {Object.entries(health.market_freshness ?? {}).map(([symbol, row]) => (
                  <div key={symbol}>
                    <span>{symbol}</span>
                    <strong className="stateGood">{ageLabel(row.latest_candle)}</strong>
                    <small>Candle {row.latest_candle ?? "—"} · Ratio {row.latest_ratio ?? "—"}</small>
                  </div>
                ))}
              </div>
            </article>
          </section>

          <section className="panel tableWrap" style={{marginTop: 14}}>
            <div className="panelHeader">
              <div><span className="label">CRON / PIPELINE JOBS</span><strong>{jobs.length} monitored</strong></div>
            </div>
            <table>
              <thead>
                <tr><th>Job</th><th>Schedule</th><th>Active</th><th>Last status</th><th>Last run</th><th>Message</th></tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.jobid}>
                    <td>{job.jobname}</td>
                    <td>{job.schedule}</td>
                    <td>{job.active ? "YES" : "NO"}</td>
                    <td className={job.last_status === "succeeded" ? "stateGood" : "stateBad"}>{job.last_status ?? "—"}</td>
                    <td>{job.last_start ?? "—"}</td>
                    <td>{job.last_message ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
