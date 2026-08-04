"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { useTheme } from "@/components/theme-provider";
import { DrawingPrimitive, type DrawingPoint, type DrawingShape, type DrawingTool } from "@/components/charts/drawing-primitive";

export type ChartTarget = { kind: "stock"; symbol: string } | { kind: "index" };

type RangeKey = "1D" | "1W" | "1M" | "3M" | "6M" | "1Y" | "3Y" | "5Y";

const RANGES: { key: RangeKey; label: string }[] = [
  { key: "1D", label: "1D" },
  { key: "1W", label: "1W" },
  { key: "1M", label: "1M" },
  { key: "3M", label: "3M" },
  { key: "6M", label: "6M" },
  { key: "1Y", label: "1Y" },
  { key: "3Y", label: "3Y" },
  { key: "5Y", label: "5Y" },
];

const DEFAULT_RANGE: RangeKey = "1D";

// 1D now means "daily candles" (1 candle = 1 day), not live intraday - it fetches the
// same full history as 5Y and relies on the readable-bar-spacing zoom below to default
// to a recent, legible window while the rest stays reachable by scrolling back.
const BACKEND_RANGE: Record<RangeKey, string> = {
  "1D": "5Y",
  "1W": "1W",
  "1M": "1M",
  "3M": "3M",
  "6M": "6M",
  "1Y": "1Y",
  "3Y": "3Y",
  "5Y": "5Y",
};

type HistoryPoint = { date: string; open: string; high: string; low: string; close: string; volume?: number };

function historyUrl(target: ChartTarget, range: RangeKey): string {
  const backendRange = BACKEND_RANGE[range];
  if (target.kind === "stock") {
    return `/api/stocks/${encodeURIComponent(target.symbol)}/history?range=${backendRange}`;
  }
  return `/api/market/index-history?range=${backendRange}`;
}

function readCssColor(varName: string): string {
  if (typeof window === "undefined") {
    return "#000000";
  }
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

function hexToRgba(hex: string, alpha: number): string {
  const parsed = hex.replace("#", "");
  const bigint = parseInt(parsed.length === 3 ? parsed.split("").map((c) => c + c).join("") : parsed, 16);
  const r = (bigint >> 16) & 255;
  const g = (bigint >> 8) & 255;
  const b = bigint & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

const READABLE_BAR_SPACING = 6;
const MAIN_PANE_HEIGHT = 300;
const VOLUME_PANE_HEIGHT = 100;

type FetchResult = {
  key: string;
  status: "ready" | "error";
  history: HistoryPoint[] | null;
};

const EMPTY_RESULT: FetchResult = { key: "", status: "error", history: null };

const TOOLS: { key: DrawingTool; label: string }[] = [
  { key: "trendline", label: "Trend Line" },
  { key: "zone", label: "S/R Zone" },
  { key: "text", label: "Text" },
];

export function TimeframeChart({ target }: { target: ChartTarget }) {
  const [range, setRange] = useState<RangeKey>(DEFAULT_RANGE);
  const [result, setResult] = useState<FetchResult>(EMPTY_RESULT);
  const [toolState, setToolState] = useState<{ key: string; tool: DrawingTool }>({ key: "", tool: "none" });
  const { theme } = useTheme();

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const drawingPrimitiveRef = useRef<DrawingPrimitive | null>(null);
  const drawingToolRef = useRef<DrawingTool>("none");
  const pendingPointRef = useRef<DrawingPoint | null>(null);
  const shapesRef = useRef<DrawingShape[]>([]);
  const nextShapeIdRef = useRef(0);

  const targetKey = target.kind === "stock" ? target.symbol : "index";
  const desiredKey = `${targetKey}:${range}`;

  const drawingTool = toolState.key === desiredKey ? toolState.tool : "none";

  useEffect(() => {
    drawingToolRef.current = drawingTool;
    if (containerRef.current) {
      containerRef.current.style.cursor = drawingTool === "none" ? "default" : "crosshair";
    }
  }, [drawingTool]);

  useEffect(() => {
    let ignore = false;

    fetch(historyUrl(target, range), { cache: "no-store" })
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
        setResult({ key: desiredKey, status: "ready", history: data as HistoryPoint[] });
      })
      .catch(() => {
        if (!ignore) {
          setResult({ key: desiredKey, status: "error", history: null });
        }
      });

    return () => {
      ignore = true;
    };
  }, [desiredKey, target, range]);

  const status = result.key !== desiredKey ? "loading" : result.status;
  const history = result.key === desiredKey ? result.history : null;

  const chartRenderable = status === "ready" && history !== null && history.length > 0;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !chartRenderable || !history) {
      return;
    }

    const showVolume = history.length > 0 && history[0].volume !== undefined;
    const totalHeight = showVolume ? MAIN_PANE_HEIGHT + VOLUME_PANE_HEIGHT : MAIN_PANE_HEIGHT;

    const chart = createChart(container, { width: container.clientWidth, height: totalHeight });
    chartRef.current = chart;

    const textColor = readCssColor("--color-text-secondary");
    const borderColor = readCssColor("--color-border");

    chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor },
      grid: { vertLines: { color: borderColor }, horzLines: { color: borderColor } },
      rightPriceScale: { borderColor },
      timeScale: { borderColor, timeVisible: false, secondsVisible: false, barSpacing: READABLE_BAR_SPACING },
    });

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
    const barCount = history.length;
    series.setData(
      history.map((point) => ({
        time: point.date,
        open: Number(point.open),
        high: Number(point.high),
        low: Number(point.low),
        close: Number(point.close),
      }))
    );

    if (showVolume) {
      const volUpColor = hexToRgba(upColor, 0.5);
      const volDownColor = hexToRgba(downColor, 0.5);
      const volumeSeries = chart.addSeries(
        HistogramSeries,
        { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false },
        1
      );
      volumeSeriesRef.current = volumeSeries;
      volumeSeries.setData(
        history.map((point) => ({
          time: point.date,
          value: point.volume ?? 0,
          color: Number(point.close) >= Number(point.open) ? volUpColor : volDownColor,
        }))
      );
      chart.panes()[1]?.setHeight(VOLUME_PANE_HEIGHT);
    } else {
      volumeSeriesRef.current = null;
    }

    const maxVisibleBars = Math.max(1, Math.floor(container.clientWidth / READABLE_BAR_SPACING));
    if (barCount > maxVisibleBars) {
      chart.timeScale().setVisibleLogicalRange({ from: barCount - maxVisibleBars, to: barCount - 1 });
    } else {
      chart.timeScale().fitContent();
    }

    const drawing = new DrawingPrimitive();
    drawingPrimitiveRef.current = drawing;
    shapesRef.current = [];
    pendingPointRef.current = null;
    const accent = readCssColor("--color-accent-primary");
    drawing.setColors(
      accent,
      hexToRgba(accent, 0.15),
      readCssColor("--color-text-primary"),
      hexToRgba(readCssColor("--color-card"), 0.9)
    );
    series.attachPrimitive(drawing);

    const finalizeShape = (kind: "line" | "rect", p1: DrawingPoint, p2: DrawingPoint) => {
      const shape: DrawingShape = { id: nextShapeIdRef.current++, kind, p1, p2 };
      shapesRef.current = [...shapesRef.current, shape];
      drawing.setShapes(shapesRef.current);
      drawing.setDraft(null);
      pendingPointRef.current = null;
      setToolState({ key: desiredKey, tool: "none" });
    };

    const handleClick = (param: MouseEventParams) => {
      const tool = drawingToolRef.current;
      if (tool === "none" || param.logical === undefined || !param.point) {
        return;
      }
      const price = series.coordinateToPrice(param.point.y);
      if (price === null) {
        return;
      }
      const point: DrawingPoint = { logical: param.logical, price };

      if (tool === "text") {
        const text = window.prompt("Annotation text:");
        if (text && text.trim()) {
          const shape: DrawingShape = { id: nextShapeIdRef.current++, kind: "text", p1: point, text: text.trim() };
          shapesRef.current = [...shapesRef.current, shape];
          drawing.setShapes(shapesRef.current);
        }
        setToolState({ key: desiredKey, tool: "none" });
        return;
      }

      if (!pendingPointRef.current) {
        pendingPointRef.current = point;
        return;
      }

      finalizeShape(tool === "trendline" ? "line" : "rect", pendingPointRef.current, point);
    };

    const handleCrosshairMove = (param: MouseEventParams) => {
      const tool = drawingToolRef.current;
      if (tool === "none" || tool === "text" || !pendingPointRef.current || param.logical === undefined || !param.point) {
        return;
      }
      const price = series.coordinateToPrice(param.point.y);
      if (price === null) {
        return;
      }
      drawing.setDraft({
        id: -1,
        kind: tool === "trendline" ? "line" : "rect",
        p1: pendingPointRef.current,
        p2: { logical: param.logical, price },
      });
    };

    chart.subscribeClick(handleClick);
    chart.subscribeCrosshairMove(handleCrosshairMove);

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      drawingPrimitiveRef.current = null;
    };
  }, [chartRenderable, history, desiredKey]);

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
      if (volumeSeriesRef.current && history) {
        const volUpColor = hexToRgba(upColor, 0.5);
        const volDownColor = hexToRgba(downColor, 0.5);
        volumeSeriesRef.current.setData(
          history.map((point) => ({
            time: point.date,
            value: point.volume ?? 0,
            color: Number(point.close) >= Number(point.open) ? volUpColor : volDownColor,
          }))
        );
      }
    }
    if (drawingPrimitiveRef.current) {
      const accent = readCssColor("--color-accent-primary");
      drawingPrimitiveRef.current.setColors(
        accent,
        hexToRgba(accent, 0.15),
        readCssColor("--color-text-primary"),
        hexToRgba(readCssColor("--color-card"), 0.9)
      );
    }
  }, [theme, history]);

  const toggleDrawingTool = (tool: DrawingTool) => {
    pendingPointRef.current = null;
    drawingPrimitiveRef.current?.setDraft(null);
    setToolState((current) => ({
      key: desiredKey,
      tool: current.key === desiredKey && current.tool === tool ? "none" : tool,
    }));
  };

  const handleClearDrawings = () => {
    shapesRef.current = [];
    pendingPointRef.current = null;
    drawingPrimitiveRef.current?.setShapes([]);
    drawingPrimitiveRef.current?.setDraft(null);
    setToolState({ key: desiredKey, tool: "none" });
  };

  return (
    <div className="flex flex-col gap-3">
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
              range === entry.key ? "bg-accent-primary text-white" : "text-text-secondary hover:text-text-primary"
            }`}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {chartRenderable && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="drawing-toolbar">
          {TOOLS.map((toolEntry) => (
            <button
              key={toolEntry.key}
              type="button"
              onClick={() => toggleDrawingTool(toolEntry.key)}
              data-testid={`drawing-tool-${toolEntry.key}`}
              aria-pressed={drawingTool === toolEntry.key}
              className={`rounded-md border px-2 py-1 text-xs font-medium transition-colors ${
                drawingTool === toolEntry.key
                  ? "border-accent-primary bg-accent-primary text-white"
                  : "border-border text-text-secondary hover:text-text-primary"
              }`}
            >
              {toolEntry.label}
            </button>
          ))}
          <button
            type="button"
            onClick={handleClearDrawings}
            data-testid="drawing-tool-clear"
            className="rounded-md border border-border px-2 py-1 text-xs font-medium text-text-secondary transition-colors hover:text-text-primary"
          >
            Clear
          </button>
          {drawingTool !== "none" && drawingTool !== "text" && (
            <span className="text-xs text-text-secondary">Click two points on the chart to place it.</span>
          )}
          {drawingTool === "text" && <span className="text-xs text-text-secondary">Click the chart to place text.</span>}
        </div>
      )}

      <div className="rounded-lg border border-border bg-card p-4">
        {status === "loading" && (
          <div className="flex h-[360px] items-center justify-center text-sm text-text-secondary">Loading chart…</div>
        )}

        {status === "error" && (
          <div className="flex h-[360px] items-center justify-center text-sm text-text-secondary">
            Chart data is temporarily unavailable.
          </div>
        )}

        {status === "ready" && (!history || history.length === 0) && (
          <div className="flex h-[360px] items-center justify-center text-sm text-text-secondary">
            No price history available for this range.
          </div>
        )}

        <div ref={containerRef} className={`w-full ${chartRenderable ? "" : "hidden"}`} data-testid="timeframe-chart" />
      </div>
    </div>
  );
}
