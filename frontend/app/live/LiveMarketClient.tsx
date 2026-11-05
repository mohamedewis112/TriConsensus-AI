"use client";

import {useEffect, useMemo, useState} from "react";
import {MarketChart} from "@/components/MarketChart";
import type {MarketSnapshot} from "@/lib/api";

const REFRESH_OPTIONS = [
  [1000, "1 sec"],
  [5000, "5 sec"],
  [15000, "15 sec"],
  [30000, "30 sec"],
  [60000, "1 min"],
  [300000, "5 min"],
] as const;

type Ticker = {
  symbol?: string;
  lastPrice?: string;
  markPrice?: string;
  indexPrice?: string;
  bid1Price?: string;
  ask1Price?: string;
  fundingRate?: string;
  openInterest?: string;
  price24hPcnt?: string;
  highPrice24h?: string;
  lowPrice24h?: string;
  volume24h?: string;
};

type LivePayload = {
  fetched_at: string;
  BTCUSDT: Ticker;
  ETHUSDT: Ticker;
};

function n(value: string | undefined, fallback = 0) {
  const parsed = Number(value ?? fallback);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function MarketPanel({
  symbol,
  title,
  research,
  live,
}: {
  symbol: "BTCUSDT" | "ETHUSDT";
  title: string;
  research: MarketSnapshot | null;
  live?: Ticker;
}) {
  const displayPrice = live?.lastPrice ? n(live.lastPrice) : research?.ticker.last_price ?? 0;
  const funding = live?.fundingRate ? n(live.fundingRate) : research?.ticker.funding_rate ?? 0;
  const oi = live?.openInterest ? n(live.openInterest) : research?.ticker.open_interest ?? 0;
  const spread = live?.bid1Price && live?.ask1Price ? n(live.ask1Price) - n(live.bid1Price) : null;
  const change = live?.price24hPcnt ? n(live.price24hPcnt) : null;

  return (
    <section className="panel chartPanel">
      <div className="panelHeader">
        <div>
          <span className="label">{title} · LIVE TICKER + 15m RESEARCH</span>
          <strong>{displayPrice ? "$" + displayPrice.toLocaleString(undefined, {maximumFractionDigits: 2}) : "Unavailable"}</strong>
          <small className="marketSource">Price display: Bybit public ticker · Research chart: Supabase collector</small>
        </div>
        <div className="microStats">
          <span>OI {oi.toLocaleString()}</span>
          <span>Funding {(funding * 100).toFixed(4)}%</span>
          <span>L/S Ratio {research ? research.latest_ratio.toFixed(3) : "—"}</span>
          <span>24h {change == null ? "—" : (change * 100).toFixed(2) + "%"}</span>
          <span>Spread {spread == null ? "—" : spread.toFixed(symbol === "BTCUSDT" ? 1 : 2)}</span>
        </div>
      </div>
      {research ? <MarketChart candles={research.candles["15m"] ?? []} /> : <div className="emptyChart">No collector data.</div>}
    </section>
  );
}

export default function LiveMarketClient({
  btc,
  eth,
}: {
  btc: MarketSnapshot | null;
  eth: MarketSnapshot | null;
}) {
  const [intervalMs, setIntervalMs] = useState(1000);
  const [paused, setPaused] = useState(false);
  const [live, setLive] = useState<LivePayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (paused) return;
    let active = true;

    const load = async () => {
      try {
        const r = await fetch("/api/live-ticker", {cache: "no-store"});
        if (!r.ok) throw new Error("Bybit live ticker unavailable");
        const payload = (await r.json()) as LivePayload;
        if (active) {
          setLive(payload);
          setError(null);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Live ticker refresh failed");
      }
    };

    load();
    const timer = window.setInterval(load, intervalMs);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [intervalMs, paused]);

  const updated = useMemo(() => live?.fetched_at ? new Date(live.fetched_at).toLocaleTimeString([], {hour12: false}) : "—", [live]);

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <p className="eyebrow">BYBIT LIVE + FROZEN RESEARCH EVIDENCE</p>
          <h1>Live Market</h1>
          <p className="muted">Choose display refresh speed without changing the frozen 15m research protocol.</p>
        </div>
        <div className="liveControls">
          <span className={"liveLamp " + (paused ? "paused" : "")}>{paused ? "PAUSED" : "LIVE"}</span>
          <select value={intervalMs} onChange={(e) => setIntervalMs(Number(e.target.value))}>
            {REFRESH_OPTIONS.map(([v, label]) => <option value={v} key={v}>{label}</option>)}
          </select>
          <button onClick={() => setPaused((p) => !p)}>{paused ? "Resume" : "Pause"}</button>
          <span className="refreshStamp">Updated {updated}</span>
        </div>
      </header>

      {error && <section className="notice">{error}. Research collector data remains available and no trading action is taken.</section>}

      <MarketPanel symbol="BTCUSDT" title="BTC / USDT" research={btc} live={live?.BTCUSDT} />
      <MarketPanel symbol="ETHUSDT" title="ETH / USDT" research={eth} live={live?.ETHUSDT} />
    </div>
  );
}
