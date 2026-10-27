import Link from "next/link";
import {getTrades} from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Trades() {
  const data = await getTrades();
  const trades = data?.trades ?? [];

  return (
    <div className="page">
      <header className="topbar">
        <div><p className="eyebrow">IMMUTABLE AUDIT TRAIL</p><h1>Trades</h1></div>
      </header>
      <section className="panel tableWrap">
        {trades.length === 0 ? (
          <div className="emptyState">No paper trades yet.</div>
        ) : (
          <table>
            <thead>
              <tr><th>Symbol</th><th>Side</th><th>Status</th><th>Entry</th><th>Net P&L</th><th>Opened</th><th>Replay</th></tr>
            </thead>
            <tbody>
              {trades.map((trade, index) => {
                const t = trade as Record<string, unknown>;
                return (
                  <tr key={String(t.id ?? index)}>
                    <td>{String(t.symbol ?? "—")}</td>
                    <td>{String(t.side ?? "—")}</td>
                    <td>{String(t.status ?? "—")}</td>
                    <td>{String(t.actual_entry ?? t.requested_entry ?? "—")}</td>
                    <td>{String(t.pnl_net ?? "—")}</td>
                    <td>{String(t.opened_at ?? "—")}</td>
                    <td>{t.consensus_decision_id ? <Link href={"/replay/" + String(t.consensus_decision_id)}>Open</Link> : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
