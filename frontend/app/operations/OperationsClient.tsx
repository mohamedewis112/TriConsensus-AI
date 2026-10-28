"use client";

import {useEffect, useMemo, useState} from "react";
import type {OperationsSnapshot, OpsEvent, ResearchOutcome} from "@/lib/operations";

const REFRESH_OPTIONS = [
  [1000, "1 sec"],
  [5000, "5 sec"],
  [15000, "15 sec"],
  [30000, "30 sec"],
  [60000, "1 min"],
  [300000, "5 min"],
] as const;

const money = (v: number) =>
  new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(v);
const pct = (v: number) => (v * 100).toFixed(2) + "%";

function researchStats(outcomes: ResearchOutcome[]) {
  const starting = 10000;
  if (!outcomes.length) {
    return {trades: 0, wins: 0, winRate: 0, profitFactor: null as number | null, expectancy: 0, compounded: 0, equity: starting, maxDrawdown: 0};
  }
  let equity = starting;
  let peak = starting;
  let maxDrawdown = 0;
  let grossWin = 0;
  let grossLoss = 0;
  let wins = 0;

  for (const row of outcomes) {
    const r = Number(row.net_return ?? 0);
    equity *= 1 + r;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, peak > 0 ? (peak - equity) / peak : 0);
    if (r > 0) { wins += 1; grossWin += r; }
    else if (r < 0) grossLoss += Math.abs(r);
  }

  const sum = outcomes.reduce((a, b) => a + Number(b.net_return ?? 0), 0);
  return {
    trades: outcomes.length,
    wins,
    winRate: wins / outcomes.length,
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : null,
    expectancy: sum / outcomes.length,
    compounded: equity / starting - 1,
    equity,
    maxDrawdown,
  };
}

function paperStats(snapshot: OperationsSnapshot) {
  const p = snapshot.finance.paper;
  const realized = Number(p.realized_pnl ?? 0);
  const starting = Number(p.starting_balance ?? 10000);
  const grossProfit = Number(p.gross_profit ?? 0);
  const grossLoss = Number(p.gross_loss ?? 0);
  const closed = Number(p.closed_trades ?? 0);
  const wins = Number(p.wins ?? 0);
  const unrealized = Number(p.unrealized_pnl ?? 0);
  return {
    starting,
    realized,
    unrealized,
    equity: starting + realized + unrealized,
    fees: Number(p.fees ?? 0),
    grossProfit,
    grossLoss,
    open: Number(p.open_positions ?? 0),
    closed,
    winRate: closed ? wins / closed : 0,
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : null,
    expectancy: closed ? realized / closed : 0,
    exposure: starting + realized > 0 ? Number(p.notional_open ?? 0) / (starting + realized) : 0,
  };
}

function stateClass(state: string) {
  const s = state.toUpperCase();
  if (["HEALTHY", "APPROVED", "LONG", "SHORT", "WIN", "OPEN"].includes(s)) return "stateGood";
  if (["NO_TRADE", "WAIT", "IDLE"].includes(s)) return "stateNeutral";
  return "stateBad";
}

function TimelineRow({event}: {event: OpsEvent}) {
  const time = new Date(event.event_time);
  return (
    <div className="opsEvent">
      <div className="opsTime">
        <strong>{time.toLocaleTimeString([], {hour12: false})}</strong>
        <span>{time.toLocaleDateString()}</span>
      </div>
      <div className="opsKind">{event.kind}</div>
      <div className="opsEventBody">
        <div className="opsEventTop">
          <strong>{event.subject}</strong>
          <span className={"opsState " + stateClass(event.state)}>{event.state}</span>
        </div>
        <span>{event.title}</span>
        <details className="opsDetails">
          <summary>Inspect event payload</summary>
          <pre>{JSON.stringify(event.details ?? {}, null, 2)}</pre>
        </details>
      </div>
    </div>
  );
}

export default function OperationsClient({initial}: {initial: OperationsSnapshot | null}) {
  const [data, setData] = useState<OperationsSnapshot | null>(initial);
  const [intervalMs, setIntervalMs] = useState(1000);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState("ALL");
  const [lastError, setLastError] = useState<string | null>(null);

  useEffect(() => {
    if (paused) return;
    let active = true;
    const load = async () => {
      try {
        const r = await fetch("/api/operations?limit=120", {cache: "no-store"});
        if (!r.ok) throw new Error("live backend unavailable");
        const next = (await r.json()) as OperationsSnapshot;
        if (active) { setData(next); setLastError(null); }
      } catch (error) {
        if (active) setLastError(error instanceof Error ? error.message : "refresh failed");
      }
    };
    const timer = window.setInterval(load, intervalMs);
    return () => { active = false; window.clearInterval(timer); };
  }, [intervalMs, paused]);

  const paper = useMemo(() => data ? paperStats(data) : null, [data]);
  const research = useMemo(() => data ? researchStats(data.finance.research_outcomes ?? []) : null, [data]);

  const filtered = useMemo(() => {
    if (!data) return [];
    if (filter === "ALL") return data.events;
    if (filter === "EXECUTION") return data.events.filter((e) => ["ORCHESTRATOR", "AGENT", "CONSENSUS", "RISK", "TRADE", "TRADE_EVENT"].includes(e.kind));
    return data.events.filter((e) => e.kind === filter);
  }, [data, filter]);

  const eth = data?.latest_signals?.find((s) => s.symbol === "ETH/USDT");
  const btc = data?.latest_signals?.find((s) => s.symbol === "BTC/USDT");
  const candidate = [btc, eth].find((s) => s && s.signal !== "NO_TRADE");

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <p className="eyebrow">LIVE OPERATIONS · V5.1-PAPER</p>
          <h1>Operations & Finance</h1>
          <p className="muted">Watch the pipeline, execution gate and financial state without exposing server credentials.</p>
        </div>
        <div className="liveControls">
          <span className={"liveLamp " + (paused ? "paused" : "")}>{paused ? "PAUSED" : "LIVE"}</span>
          <select value={intervalMs} onChange={(e) => setIntervalMs(Number(e.target.value))}>
            {REFRESH_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
          <button onClick={() => setPaused((p) => !p)}>{paused ? "Resume" : "Pause"}</button>
        </div>
      </header>

      {lastError && <section className="notice">Live refresh failed closed: {lastError}</section>}

      {candidate && (
        <section className="candidateBanner">
          <div>
            <span className="label">ACTIVE PROSPECTIVE CANDIDATE</span>
            <strong>{candidate.symbol} · {candidate.signal}</strong>
            <small>Context {new Date(candidate.context_timestamp).toLocaleString()} · feature {Number(candidate.feature_value).toFixed(5)}</small>
          </div>
          <div className="candidateState">
            <span>Agent runtime</span>
            <strong>{(data?.agents?.length ?? 0) > 0 ? "EXECUTED" : "NOT EXECUTED"}</strong>
            <small>{(data?.agents?.length ?? 0) > 0 ? "See agent decisions below." : "Fail-closed until external agent runtime is available."}</small>
          </div>
        </section>
      )}

      <section className="financeGrid">
        <article className="panel financeHero">
          <span className="label">PAPER ACCOUNT</span>
          <strong>{paper ? money(paper.equity) : "—"}</strong>
          <span className={paper && paper.realized < 0 ? "negativeText" : "positiveText"}>
            {paper ? (paper.realized >= 0 ? "+" : "") + money(paper.realized) + " realized · " + (paper.unrealized >= 0 ? "+" : "") + money(paper.unrealized) + " unrealized" : "—"}
          </span>
          <div className="financeMini">
            <div><span>Starting capital</span><strong>{paper ? money(paper.starting) : "—"}</strong></div>
            <div><span>Closed</span><strong>{paper?.closed ?? 0}</strong></div>
            <div><span>Open</span><strong>{paper?.open ?? 0}</strong></div>
            <div><span>Win rate</span><strong>{paper ? pct(paper.winRate) : "—"}</strong></div>
            <div><span>PF</span><strong>{paper?.profitFactor == null ? "—" : Number.isFinite(paper.profitFactor) ? paper.profitFactor.toFixed(2) : "∞"}</strong></div>
            <div><span>Expectancy</span><strong>{paper ? money(paper.expectancy) : "—"}</strong></div>
            <div><span>Gross profit</span><strong>{paper ? money(paper.grossProfit) : "—"}</strong></div>
            <div><span>Gross loss</span><strong>{paper ? money(paper.grossLoss) : "—"}</strong></div>
            <div><span>Fees</span><strong>{paper ? money(paper.fees) : "—"}</strong></div>
            <div><span>Exposure</span><strong>{paper ? pct(paper.exposure) : "—"}</strong></div>
          </div>
        </article>

        <article className="panel financeHero">
          <span className="label">RESEARCH OOS · HYPOTHETICAL $10K</span>
          <strong>{research ? money(research.equity) : "—"}</strong>
          <span className={research && research.compounded < 0 ? "negativeText" : "positiveText"}>
            {research ? (research.compounded >= 0 ? "+" : "") + pct(research.compounded) + " compounded" : "—"}
          </span>
          <div className="financeMini">
            <div><span>Trades</span><strong>{research?.trades ?? 0}</strong></div>
            <div><span>Wins</span><strong>{research?.wins ?? 0}</strong></div>
            <div><span>Win rate</span><strong>{research ? pct(research.winRate) : "—"}</strong></div>
            <div><span>PF</span><strong>{research?.profitFactor == null ? "—" : Number.isFinite(research.profitFactor) ? research.profitFactor.toFixed(2) : "∞"}</strong></div>
            <div><span>Expectancy</span><strong>{research ? pct(research.expectancy) : "—"}</strong></div>
            <div><span>Max DD</span><strong>{research ? pct(research.maxDrawdown) : "—"}</strong></div>
          </div>
        </article>
      </section>

      <section className="opsSplit">
        <article className="panel">
          <div className="panelHeader">
            <div>
              <span className="label">LIVE EVENT STREAM</span>
              <strong>{data?.health?.status?.toUpperCase() ?? "UNKNOWN"}</strong>
            </div>
            <div className="opsFilters">
              {["ALL", "SIGNAL", "EXECUTION", "WATCHDOG", "OOS_OUTCOME"].map((name) => (
                <button className={filter === name ? "active" : ""} onClick={() => setFilter(name)} key={name}>{name}</button>
              ))}
            </div>
          </div>
          <div className="opsTimeline">
            {filtered.length ? filtered.map((event, i) => <TimelineRow event={event} key={event.event_time + "-" + event.kind + "-" + i} />) : <div className="emptyState">No events in this filter.</div>}
          </div>
        </article>

        <aside className="opsSide">
          <article className="panel">
            <span className="label">AGENT STATES</span>
            <div className="agentStateList">
              {data?.agents?.length ? data.agents.map((agent) => (
                <div key={agent.agent_role}>
                  <span>{agent.agent_role}</span>
                  <strong className={stateClass(agent.decision ?? "FAILED")}>{agent.decision ?? "FAILED"}</strong>
                  <small>{agent.confidence == null ? "—" : (Number(agent.confidence) * 100).toFixed(0) + "%"} · {agent.latency_ms ?? "—"} ms</small>
                </div>
              )) : (
                <>
                  <div><span>Market Intelligence</span><strong className="stateNeutral">IDLE</strong><small>No run recorded</small></div>
                  <div><span>Trade Strategist</span><strong className="stateNeutral">IDLE</strong><small>No run recorded</small></div>
                  <div><span>Risk & Devil&apos;s Advocate</span><strong className="stateNeutral">IDLE</strong><small>No run recorded</small></div>
                </>
              )}
            </div>
          </article>

          <article className="panel">
            <span className="label">LATEST SIGNALS</span>
            <div className="signalStateList">
              {[btc, eth].filter(Boolean).map((signal) => (
                <div key={signal!.symbol}>
                  <span>{signal!.symbol}</span>
                  <strong className={stateClass(signal!.signal)}>{signal!.signal}</strong>
                  <small>{Number(signal!.feature_value).toFixed(5)}</small>
                </div>
              ))}
            </div>
          </article>

          <article className="panel">
            <span className="label">EXECUTION GATE</span>
            <div className="safetyGrid">
              <span>Consensus</span><strong>{String(data?.latest_consensus?.outcome ?? "NONE")}</strong>
              <span>Full consensus</span><strong>{data?.latest_consensus?.full_consensus === true ? "YES" : "NO"}</strong>
              <span>Risk engine</span><strong>{data?.latest_risk?.approved === true ? "APPROVED" : data?.latest_risk?.approved === false ? "VETO" : "NOT RUN"}</strong>
              <span>Paper positions</span><strong>{data?.open_trades?.length ?? 0}</strong>
            </div>
          </article>

          <article className="panel">
            <span className="label">OPEN PAPER POSITIONS</span>
            {data?.open_trades?.length ? (
              <div className="agentStateList">
                {data.open_trades.map((trade, index) => (
                  <div key={String(trade.id ?? index)}>
                    <span>{String(trade.symbol ?? "—")} · {String(trade.side ?? "—")}</span>
                    <strong className="stateGood">OPEN</strong>
                    <small>
                      Entry {String(trade.actual_entry ?? trade.requested_entry ?? "—")} ·
                      SL {String(trade.stop_loss ?? "—")} ·
                      TP {String(trade.take_profit_1 ?? "—")}
                    </small>
                  </div>
                ))}
              </div>
            ) : (
              <div className="refreshStamp">No open paper positions.</div>
            )}
          </article>

          <article className="panel">
            <span className="label">LAST REFRESH</span>
            <div className="refreshStamp">{data?.generated_at ? new Date(data.generated_at).toLocaleString() : "—"}</div>
          </article>
        </aside>
      </section>
    </div>
  );
}
