import {MarketChart} from "@/components/MarketChart";
import {getMarket, getSummary} from "@/lib/api";

export const dynamic = "force-dynamic";

const money = (v: number) =>
  new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(v);
const pct = (v: number | null | undefined) =>
  v == null ? "—" : (v * 100).toFixed(3) + "%";
const num = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? "—" : v.toFixed(2);

export default async function Dashboard() {
  const [summary, btc] = await Promise.all([getSummary(), getMarket("BTCUSDT")]);
  const forward = summary?.forward;

  const cards = summary && forward
    ? [
        ["Watchdog", forward.health_status.toUpperCase()],
        ["BTC Signal", forward.btc_signal],
        ["ETH Signal", forward.eth_signal],
        ["BTC OOS Trades", String(forward.btc_trade_count)],
        ["ETH OOS Trades", String(forward.eth_trade_count)],
        ["BTC Mean Net", pct(forward.btc_mean_net_return)],
        ["ETH Mean Net", pct(forward.eth_mean_net_return)],
        ["BTC Profit Factor", num(forward.btc_profit_factor)],
        ["ETH Profit Factor", num(forward.eth_profit_factor)],
        ["Paper Trades", String(forward.paper_trades)],
        ["Agent Runs", String(forward.agent_runs)],
        ["Consensus", String(forward.consensus_decisions)],
      ]
    : [];

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <p className="eyebrow">V5.1 · FORWARD TESTING</p>
          <h1>Research Terminal</h1>
          <p className="muted">Frozen protocol. Prospective evidence. Fail closed.</p>
        </div>
        <div className="statusPill"><span className="pulseDot" /> PAPER MODE</div>
      </header>

      {!summary && (
        <section className="notice">
          Secure dashboard backend is unavailable. No performance data is fabricated.
        </section>
      )}

      <section className="metrics">
        {cards.map(([label, value]) => (
          <article className="metricCard" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </section>

      <section className="panel chartPanel">
        <div className="panelHeader">
          <div>
            <span className="label">BTC / USDT · 15m · COLLECTOR</span>
            <strong>{btc ? money(btc.ticker.last_price) : "No live feed"}</strong>
          </div>
          {btc && (
            <div className="microStats">
              <span>OI {btc.ticker.open_interest.toLocaleString()}</span>
              <span>Funding {(btc.ticker.funding_rate * 100).toFixed(4)}%</span>
              <span>L/S Ratio {btc.latest_ratio.toFixed(3)}</span>
            </div>
          )}
        </div>
        {btc ? (
          <MarketChart candles={btc.candles["15m"] ?? []} />
        ) : (
          <div className="emptyChart">Awaiting protected market evidence.</div>
        )}
      </section>

      <section className="twoCol">
        <article className="panel">
          <span className="label">Decision Gate</span>
          <div className="flow">
            <div>Frozen V5.1 Signal</div><b>→</b>
            <div>Market Intelligence</div><b>→</b>
            <div>Strategist</div><b>→</b>
            <div>Devil&apos;s Advocate</div><b>→</b>
            <div>Risk Engine</div>
          </div>
        </article>
        <article className="panel">
          <span className="label">Safety State</span>
          <div className="safetyGrid">
            <span>Live money</span><strong className="danger">DISABLED</strong>
            <span>Consensus</span><strong>FULL ONLY</strong>
            <span>Failure policy</span><strong>NO TRADE</strong>
            <span>BTC gate</span><strong>{forward?.btc_gate ?? "UNKNOWN"}</strong>
            <span>ETH gate</span><strong>{forward?.eth_gate ?? "UNKNOWN"}</strong>
          </div>
        </article>
      </section>
    </div>
  );
}
