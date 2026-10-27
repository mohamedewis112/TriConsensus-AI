"use client";

import {useEffect, useRef} from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  type CandlestickData,
  type UTCTimestamp,
} from "lightweight-charts";
import type {Candle} from "@/lib/api";

export function MarketChart({candles}: {candles: Candle[]}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || candles.length === 0) return;

    const chart = createChart(ref.current, {
      height: 330,
      layout: {
        background: {type: ColorType.Solid, color: "#07111f"},
        textColor: "#8fa2b8",
        attributionLogo: true,
      },
      grid: {
        vertLines: {color: "#142033"},
        horzLines: {color: "#142033"},
      },
      rightPriceScale: {borderColor: "#223047"},
      timeScale: {borderColor: "#223047", timeVisible: true},
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#21d19f",
      downColor: "#ff5e78",
      borderVisible: false,
      wickUpColor: "#21d19f",
      wickDownColor: "#ff5e78",
    });

    const data: CandlestickData[] = candles.map((c) => ({
      time: Math.floor(c.start_ms / 1000) as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    series.setData(data);
    chart.timeScale().fitContent();

    const resize = new ResizeObserver(([entry]) => {
      chart.applyOptions({width: entry.contentRect.width});
    });
    resize.observe(ref.current);

    return () => {
      resize.disconnect();
      chart.remove();
    };
  }, [candles]);

  return <div ref={ref} className="marketChart" />;
}
