import {MarketChart} from "@/components/MarketChart";
import {getMarket} from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function LiveMarket() {
  const [btc, eth] = await Promise.all([getMarket("BTCUSDT"), getMarket("ETHUSDT")]);
  const markets = [["BTC / USDT", btc], ["ETH / USDT", eth]] as const;

  return (
    <div className="page">
      <header className="topbar">
        <div><p className="eyebrow">BYBIT · LINEAR PERPETUAL</p><h1>Live Market</h1></div>
      </header>
      {markets.map(([name, market]) => (
        <section className="panel chartPanel" key={name}>
          <div className="panelHeader">
            <div>
              <span className="label">{name} · 15m</span>
              <strong>{market ? "$" + market.ticker.last_price.toLocaleString() : "Unavailable"}</strong>
            </div>
            {market && (
              <div className="microStats">
                <span>OI {market.ticker.open_interest.toLocaleString()}</span>
                <span>Spread {market.orderbook.spread_bps.toFixed(2)} bps</span>
                <span>Book {(market.orderbook.imbalance * 100).toFixed(1)}%</span>
              </div>
            )}
          </div>
          {market ? <MarketChart candles={market.candles["15m"] ?? []} /> : <div className="emptyChart">No market data.</div>}
        </section>
      ))}
    </div>
  );
}
