import {getSummary} from "@/lib/api";

export const dynamic = "force-dynamic";

const pct = (v: number | null | undefined) =>
  v == null ? "—" : (v * 100).toFixed(3) + "%";
const num = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? "—" : v.toFixed(2);

export default async function Experiments() {
  const summary = await getSummary();
  const forward = summary?.forward;

  return (
    <div className="page">
      <header className="topbar">
        <div><p className="eyebrow">IMMUTABLE CONFIGURATION</p><h1>V5.1 Forward Test</h1></div>
      </header>
      <section className="panel">
        <div className="experimentRow">
          <div>
            <strong>V5.1-PAPER</strong>
            <span>BTC + ETH · 15m context · 180m horizon · 15 bps cost · Paper only</span>
          </div>
          <div><span className="label">Status</span><strong>{summary?.experiment_status ?? "UNAVAILABLE"}</strong></div>
          <div><span className="label">Watchdog</span><strong>{forward?.health_status?.toUpperCase() ?? "UNKNOWN"}</strong></div>
          <div><span className="label">Paper trades</span><strong>{forward?.paper_trades ?? 0}</strong></div>
        </div>
      </section>

      <section className="twoCol">
        <article className="panel">
          <span className="label">BTC / USDT</span>
          <div className="safetyGrid">
            <span>Prospective signal</span><strong>{forward?.btc_signal ?? "UNKNOWN"}</strong>
            <span>OOS evaluated</span><strong>{forward?.btc_trade_count ?? 0}</strong>
            <span>Mean net</span><strong>{pct(forward?.btc_mean_net_return)}</strong>
            <span>Profit factor</span><strong>{num(forward?.btc_profit_factor)}</strong>
            <span>Gate</span><strong>{forward?.btc_gate ?? "UNKNOWN"}</strong>
          </div>
        </article>
        <article className="panel">
          <span className="label">ETH / USDT</span>
          <div className="safetyGrid">
            <span>Prospective signal</span><strong>{forward?.eth_signal ?? "UNKNOWN"}</strong>
            <span>OOS evaluated</span><strong>{forward?.eth_trade_count ?? 0}</strong>
            <span>Mean net</span><strong>{pct(forward?.eth_mean_net_return)}</strong>
            <span>Profit factor</span><strong>{num(forward?.eth_profit_factor)}</strong>
            <span>Gate</span><strong>{forward?.eth_gate ?? "UNKNOWN"}</strong>
          </div>
        </article>
      </section>
    </div>
  );
}
