"use client";

import { CandlestickSeries, ColorType, createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { useEffect, useRef } from "react";
import { useTheme } from "@/components/theme-provider";
import type { StockHistoryPoint } from "@/lib/market-data";

// TODO: upgrade to TradingView Advanced Charts once the licensed library
// application is approved. Using the free, unrestricted Lightweight Charts
// build for now.

function readCssColor(varName: string): string {
  if (typeof window === "undefined") {
    return "#000000";
  }
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

function applyThemeColors(chart: IChartApi, series: ISeriesApi<"Candlestick">) {
  const textColor = readCssColor("--color-text-secondary");
  const borderColor = readCssColor("--color-border");
  const upColor = readCssColor("--color-success-text");
  const downColor = readCssColor("--color-danger-text");

  chart.applyOptions({
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor,
    },
    grid: {
      vertLines: { color: borderColor },
      horzLines: { color: borderColor },
    },
    rightPriceScale: { borderColor },
    timeScale: { borderColor },
  });

  series.applyOptions({
    upColor,
    downColor,
    borderUpColor: upColor,
    borderDownColor: downColor,
    wickUpColor: upColor,
    wickDownColor: downColor,
  });
}

export function PriceChart({ history }: { history: StockHistoryPoint[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const { theme } = useTheme();

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 360,
    });
    chartRef.current = chart;

    const series = chart.addSeries(CandlestickSeries);
    seriesRef.current = series;
    applyThemeColors(chart, series);

    series.setData(
      history.map((point) => ({
        time: point.date,
        open: Number(point.open),
        high: Number(point.high),
        low: Number(point.low),
        close: Number(point.close),
      }))
    );

    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [history]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) {
      return;
    }
    applyThemeColors(chart, series);
  }, [theme]);

  return <div ref={containerRef} className="w-full" data-testid="price-chart" />;
}
