import {MarketChart} from "@/components/MarketChart";
import {getMarket, getSummary} from "@/lib/api";

export const dynamic = "force-dynamic";

const money = (v: number) =>
  new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(v);
const pct = (v: number) => (v * 100).toFixed(2) + "%";

export default async function Dashboard() {
  const [summary, btc] = await Promise.all([getSummary(), getMarket("BTCUSDT")]);

  const cards = summary
    ? [
        ["Paper Balance", money(summary.paper_balance)],
        ["Net P&L", money(summary.net_pnl)],
        ["Today's P&L", money(summary.today_pnl)],
        ["Win Rate", pct(summary.win_rate)],
        ["Profit Factor", summary.profit_factor?.toFixed(2) ?? "—"],
        ["Expectancy", money(summary.expectancy)],
        ["Max Drawdown", pct(summary.max_drawdown)],
        ["Sharpe", summary.sharpe?.toFixed(2) ?? "—"],
        ["Sortino", summary.sortino?.toFixed(2) ?? "—"],
        ["Open Positions", String(summary.open_positions)],
        ["Closed Trades", String(summary.closed_trades)],
        ["Losing Streak", String(summary.max_losing_streak)],
      ]
    : [];

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <p className="eyebrow">V1 · FORWARD TESTING</p>
          <h1>Research Terminal</h1>
          <p className="muted">Evidence first. Full consensus. Deterministic risk.</p>
        </div>
        <div className="statusPill"><span className="pulseDot" /> PAPER MODE</div>
      </header>

      {!summary && (
        <section className="notice">
          Backend credentials are not configured yet. No performance data is fabricated.
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
            <span className="label">BTC / USDT · 15m</span>
            <strong>{btc ? money(btc.ticker.last_price) : "No live feed"}</strong>
          </div>
          {btc && (
            <div className="microStats">
              <span>Spread {btc.orderbook.spread_bps.toFixed(2)} bps</span>
              <span>OB Imbalance {(btc.orderbook.imbalance * 100).toFixed(1)}%</span>
              <span>Funding {(btc.ticker.funding_rate * 100).toFixed(4)}%</span>
            </div>
          )}
        </div>
        {btc ? (
          <MarketChart candles={btc.candles["15m"] ?? []} />
        ) : (
          <div className="emptyChart">Awaiting protected Bybit feed.</div>
        )}
      </section>

      <section className="twoCol">
        <article className="panel">
          <span className="label">Decision Gate</span>
          <div className="flow">
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
            <span>Max positions</span><strong>1</strong>
          </div>
        </article>
      </section>
    </div>
  );
}
