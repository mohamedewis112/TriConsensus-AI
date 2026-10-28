import Link from "next/link";
import {getTrades} from "@/lib/api";

export const dynamic = "force-dynamic";

const money = (value: unknown) => {
  const n = Number(value);
  return Number.isFinite(n) ? "$" + n.toLocaleString(undefined, {maximumFractionDigits: 2}) : "—";
};

export default async function Trades() {
  const data = await getTrades();
  const trades = data?.trades ?? [];

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <p className="eyebrow">IMMUTABLE PAPER AUDIT TRAIL</p>
          <h1>Trades</h1>
          <p className="muted">Every paper execution remains linked to consensus, risk evaluation and replay evidence.</p>
        </div>
      </header>

      <section className="panel tableWrap">
        {trades.length === 0 ? (
          <div className="emptyState">No paper trades yet. Candidates remain fail-closed until the agent runtime can complete full consensus.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Symbol</th><th>Side</th><th>Status</th><th>Entry</th><th>Exit</th>
                <th>SL</th><th>TP1</th><th>Size</th><th>Net P&L</th><th>Fees</th>
                <th>Duration</th><th>Close reason</th><th>Replay</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((trade, index) => {
                const t = trade as Record<string, unknown>;
                return (
                  <tr key={String(t.id ?? index)}>
                    <td>{String(t.symbol ?? "—")}</td>
                    <td>{String(t.side ?? "—")}</td>
                    <td>{String(t.status ?? "—")}</td>
                    <td>{money(t.actual_entry ?? t.requested_entry)}</td>
                    <td>{money(t.actual_exit ?? t.requested_exit)}</td>
                    <td>{money(t.stop_loss)}</td>
                    <td>{money(t.take_profit_1)}</td>
                    <td>{String(t.position_size ?? "—")}</td>
                    <td className={Number(t.pnl_net ?? 0) >= 0 ? "stateGood" : "stateBad"}>{money(t.pnl_net)}</td>
                    <td>{money(t.realized_fees ?? t.estimated_fees)}</td>
                    <td>{t.trade_duration_seconds == null ? "—" : String(t.trade_duration_seconds) + "s"}</td>
                    <td>{String(t.close_reason ?? "—")}</td>
                    <td>{t.consensus_decision_id ? <Link href={"/replay/" + String(t.consensus_decision_id)}>Replay</Link> : "—"}</td>
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
