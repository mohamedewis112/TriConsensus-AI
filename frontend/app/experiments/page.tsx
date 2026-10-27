import {getSummary} from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Experiments() {
  const summary = await getSummary();
  return (
    <div className="page">
      <header className="topbar">
        <div><p className="eyebrow">IMMUTABLE CONFIGURATIONS</p><h1>Experiments</h1></div>
      </header>
      <section className="panel">
        <div className="experimentRow">
          <div><strong>V1</strong><span>BTC + ETH · 15m setup · Paper only</span></div>
          <div><span className="label">Status</span><strong>{summary?.experiment_status ?? "NOT STARTED"}</strong></div>
          <div><span className="label">Closed trades</span><strong>{summary?.closed_trades ?? 0}</strong></div>
          <div><span className="label">Net P&L</span><strong>{summary ? "$" + summary.net_pnl.toFixed(2) : "—"}</strong></div>
        </div>
      </section>
    </div>
  );
}
