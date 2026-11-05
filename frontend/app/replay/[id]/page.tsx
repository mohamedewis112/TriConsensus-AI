import {getReplay} from "@/lib/api";

export const dynamic = "force-dynamic";

function block(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2);
}

export default async function Replay({params}: {params: Promise<{id: string}>}) {
  const {id} = await params;
  const replay = await getReplay(id);

  if (!replay) {
    return (
      <div className="page">
        <header className="topbar">
          <div><p className="eyebrow">DECISION REPLAY</p><h1>{id.slice(0, 12)}…</h1></div>
        </header>
        <section className="notice">Decision not found or secure replay backend is unavailable.</section>
      </div>
    );
  }

  const consensus = replay.consensus as Record<string, unknown> | undefined;
  const market = replay.market_snapshot as Record<string, unknown> | undefined;
  const marketAgent = replay.market_agent as Record<string, unknown> | undefined;
  const strategist = replay.strategist_agent as Record<string, unknown> | undefined;
  const riskAgent = replay.risk_agent as Record<string, unknown> | undefined;
  const ml = replay.ml_prediction as Record<string, unknown> | undefined;
  const risk = replay.risk as Record<string, unknown> | undefined;
  const trade = replay.trade as Record<string, unknown> | undefined;
  const tradeEvents = replay.trade_events as unknown[] | undefined;

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <p className="eyebrow">DECISION BLACK BOX</p>
          <h1>{id.slice(0, 12)}…</h1>
          <p className="muted">Immutable replay from market snapshot through agents, consensus, risk and trade result.</p>
        </div>
        <div className="statusPill">{String(consensus?.outcome ?? "UNKNOWN")}</div>
      </header>

      <section className="replayGrid">
        {[
          ["Consensus", consensus],
          ["Market snapshot", market],
          ["Market Intelligence", marketAgent],
          ["Trade Strategist", strategist],
          ["Risk & Devil's Advocate", riskAgent],
          ["ML evidence", ml],
          ["Deterministic risk", risk],
          ["Paper trade", trade],
          ["Trade events", tradeEvents ?? []],
        ].map(([label, value]) => (
          <article className="panel replayCard" key={String(label)}>
            <span className="label">{String(label)}</span>
            <pre>{block(value)}</pre>
          </article>
        ))}
      </section>
    </div>
  );
}
