import {getReplay} from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Replay({params}: {params: Promise<{id: string}>}) {
  const {id} = await params;
  const replay = await getReplay(id);

  return (
    <div className="page">
      <header className="topbar">
        <div><p className="eyebrow">DECISION REPLAY</p><h1>{id.slice(0, 12)}…</h1></div>
      </header>
      {!replay ? (
        <section className="notice">Decision not found or backend is unavailable.</section>
      ) : (
        <section className="replayGrid">
          {[
            ["Consensus", replay.outcome],
            ["Consensus reason", replay.consensus_reason],
            ["Market agent", replay.market_agent_output],
            ["Strategist", replay.strategist_agent_output],
            ["Devil's advocate", replay.risk_agent_output],
            ["ML evidence", replay.ml_prediction],
            ["Risk checks", replay.risk_checks],
            ["Trade result", {
              status: replay.trade_status,
              pnl_net: replay.pnl_net,
              fees: replay.realized_fees,
              slippage: replay.realized_slippage,
              mae: replay.mae,
              mfe: replay.mfe,
            }],
          ].map(([label, value]) => (
            <article className="panel replayCard" key={String(label)}>
              <span className="label">{String(label)}</span>
              <pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
