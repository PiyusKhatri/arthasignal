"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { useTheme } from "@/components/theme-provider";

export type ChartTarget = { kind: "stock"; symbol: string } | { kind: "index" };

type RangeKey = "1D" | "1W" | "1M" | "3M" | "6M" | "1Y" | "3Y" | "5Y" | "ALL";

const RANGES: { key: RangeKey; label: string }[] = [
  { key: "1D", label: "1D" },
  { key: "1W", label: "1W" },
  { key: "1M", label: "1M" },
  { key: "3M", label: "3M" },
  { key: "6M", label: "6M" },
  { key: "1Y", label: "1Y" },
  { key: "3Y", label: "3Y" },
  { key: "5Y", label: "5Y" },
  { key: "ALL", label: "All" },
];

const DEFAULT_RANGE: RangeKey = "6M";

type HistoryPoint = { date: string; open: string; high: string; low: string; close: string };
type IntradayPoint = { time: number; price: string };
type IntradayResponse = { has_data: boolean; points: IntradayPoint[] };

function historyUrl(target: ChartTarget, range: RangeKey): string {
  if (target.kind === "stock") {
    return `/api/stocks/${encodeURIComponent(target.symbol)}/history?range=${range}`;
  }
  return `/api/market/index-history?range=${range}`;
}

function intradayUrl(target: ChartTarget): string {
  if (target.kind === "stock") {
    return `/api/stocks/${encodeURIComponent(target.symbol)}/intraday-today`;
  }
  return `/api/market/index-intraday-today`;
}

function readCssColor(varName: string): string {
  if (typeof window === "undefined") {
    return "#000000";
  }
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

type FetchResult = {
  key: string;
  status: "ready" | "error";
  history: HistoryPoint[] | null;
  intraday: IntradayResponse | null;
};

const EMPTY_RESULT: FetchResult = { key: "", status: "error", history: null, intraday: null };

export function TimeframeChart({ target }: { target: ChartTarget }) {
  const [range, setRange] = useState<RangeKey>(DEFAULT_RANGE);
  const [result, setResult] = useState<FetchResult>(EMPTY_RESULT);
  const { theme } = useTheme();

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lineSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  const isIntraday = range === "1D";
  const targetKey = target.kind === "stock" ? target.symbol : "index";
  const desiredKey = `${targetKey}:${range}`;

  useEffect(() => {
    let ignore = false;

    const url = isIntraday ? intradayUrl(target) : historyUrl(target, range);
    fetch(url, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`request failed with status ${response.status}`);
        }
        return response.json();
      })
      .then((data) => {
        if (ignore) {
          return;
        }
        setResult({
          key: desiredKey,
          status: "ready",
          history: isIntraday ? null : (data as HistoryPoint[]),
          intraday: isIntraday ? (data as IntradayResponse) : null,
        });
      })
      .catch(() => {
        if (!ignore) {
          setResult({ key: desiredKey, status: "error", history: null, intraday: null });
        }
      });

    return () => {
      ignore = true;
    };
  }, [desiredKey, isIntraday, target, range]);

  const status = result.key !== desiredKey ? "loading" : result.status;
  const history = result.key === desiredKey ? result.history : null;
  const intraday = result.key === desiredKey ? result.intraday : null;

  const intradayHasData = isIntraday && intraday !== null && intraday.has_data && intraday.points.length > 0;
  const chartRenderable = status === "ready" && (isIntraday ? intradayHasData : history !== null && history.length > 0);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !chartRenderable) {
      return;
    }

    const chart = createChart(container, { width: container.clientWidth, height: 360 });
    chartRef.current = chart;

    const textColor = readCssColor("--color-text-secondary");
    const borderColor = readCssColor("--color-border");

    chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor },
      grid: { vertLines: { color: borderColor }, horzLines: { color: borderColor } },
      rightPriceScale: { borderColor },
      timeScale: { borderColor, timeVisible: isIntraday, secondsVisible: false },
    });

    if (isIntraday && intraday) {
      const lineColor = readCssColor("--color-accent-text");
      const series = chart.addSeries(LineSeries, { color: lineColor, lineWidth: 2 });
      lineSeriesRef.current = series;
      candleSeriesRef.current = null;
      series.setData(
        intraday.points.map((point) => ({
          time: point.time as UTCTimestamp,
          value: Number(point.price),
        }))
      );
    } else if (history) {
      const upColor = readCssColor("--color-success-text");
      const downColor = readCssColor("--color-danger-text");
      const series = chart.addSeries(CandlestickSeries, {
        upColor,
        downColor,
        borderUpColor: upColor,
        borderDownColor: downColor,
        wickUpColor: upColor,
        wickDownColor: downColor,
      });
      candleSeriesRef.current = series;
      lineSeriesRef.current = null;
      series.setData(
        history.map((point) => ({
          time: point.date,
          open: Number(point.open),
          high: Number(point.high),
          low: Number(point.low),
          close: Number(point.close),
        }))
      );
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      lineSeriesRef.current = null;
    };
  }, [chartRenderable, isIntraday, history, intraday]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) {
      return;
    }
    const textColor = readCssColor("--color-text-secondary");
    const borderColor = readCssColor("--color-border");
    chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor },
      grid: { vertLines: { color: borderColor }, horzLines: { color: borderColor } },
      rightPriceScale: { borderColor },
      timeScale: { borderColor },
    });

    if (candleSeriesRef.current) {
      const upColor = readCssColor("--color-success-text");
      const downColor = readCssColor("--color-danger-text");
      candleSeriesRef.current.applyOptions({
        upColor,
        downColor,
        borderUpColor: upColor,
        borderDownColor: downColor,
        wickUpColor: upColor,
        wickDownColor: downColor,
      });
    }
    if (lineSeriesRef.current) {
      lineSeriesRef.current.applyOptions({ color: readCssColor("--color-accent-text") });
    }
  }, [theme]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div
          className="inline-flex flex-wrap items-center gap-0.5 rounded-full border border-border bg-card p-0.5"
          data-testid="timeframe-selector"
        >
          {RANGES.map((entry) => (
            <button
              key={entry.key}
              type="button"
              onClick={() => setRange(entry.key)}
              data-testid={`timeframe-${entry.key}`}
              aria-pressed={range === entry.key}
              className={`rounded-full px-2.5 py-1 text-xs font-medium transition-colors ${
                range === entry.key
                  ? "bg-accent-primary text-white"
                  : "text-text-secondary hover:text-text-primary"
              }`}
            >
              {entry.label}
            </button>
          ))}
        </div>
        {isIntraday && (
          <span className="whitespace-nowrap text-xs font-medium text-success-text" data-testid="today-live-label">
            Today (live)
          </span>
        )}
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        {status === "loading" && (
          <div className="flex h-[360px] items-center justify-center text-sm text-text-secondary">Loading chart…</div>
        )}

        {status === "error" && (
          <div className="flex h-[360px] items-center justify-center text-sm text-text-secondary">
            Chart data is temporarily unavailable.
          </div>
        )}

        {status === "ready" && isIntraday && !intradayHasData && (
          <div
            className="flex h-[360px] items-center justify-center text-sm text-text-secondary"
            data-testid="intraday-empty-state"
          >
            Today&apos;s live data isn&apos;t available yet.
          </div>
        )}

        {status === "ready" && !isIntraday && (!history || history.length === 0) && (
          <div className="flex h-[360px] items-center justify-center text-sm text-text-secondary">
            No price history available for this range.
          </div>
        )}

        <div ref={containerRef} className={`w-full ${chartRenderable ? "" : "hidden"}`} data-testid="timeframe-chart" />
      </div>
    </div>
  );
}
