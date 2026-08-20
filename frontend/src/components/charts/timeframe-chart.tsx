"use client";

import {
  AreaSeries,
  BarSeries,
  BaselineSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "@/components/theme-provider";
import {
  DrawingPrimitive,
  type DrawingPoint,
  type DrawingShape,
  type DrawingTool,
} from "@/components/charts/drawing-primitive";
import {
  aggregateIntraday,
  autoLevels,
  calculateIndicators,
  detectPatterns,
  filterBarsByRange,
  heikinAshi,
  normalizeToPercent,
  resampleBars,
  type ChartBar,
  type IndicatorBundle,
  type IndicatorPoint,
} from "@/components/charts/chart-math";

export type ChartTarget = { kind: "stock"; symbol: string } | { kind: "index" };

export type ArthaChartSnapshot = {
  score: number;
  rating: string;
  confidence: string;
  asOfDate: string | null;
  signals: Array<{ signalName: string; status: string; entryDate: string | null }>;
};

type IntervalKey = "5m" | "15m" | "30m" | "1H" | "1D" | "1W" | "1M";
type RangeKey = "1D" | "5D" | "1M" | "3M" | "6M" | "YTD" | "1Y" | "3Y" | "5Y" | "ALL";
type ChartStyle = "candles" | "heikin" | "bars" | "line" | "area" | "baseline";
type ScaleMode = "linear" | "log" | "percent";
type IndicatorKey =
  | "sma20" | "sma50" | "sma100" | "sma200" | "ema20" | "ema50"
  | "bollinger" | "vwap20" | "rsi14" | "macd" | "stochastic"
  | "atr14" | "obv" | "cci20" | "roc12" | "highLow52" | "pivots" | "fib";

type HistoryPoint = { date: string; open: string | number; high: string | number; low: string | number; close: string | number; volume?: number };
type IntradayPoint = { time: number; price: string | number; volume?: number };
type IntradayResponse = { has_data: boolean; points: IntradayPoint[] };

type HoverSnapshot = {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  changePercent: number | null;
  indicators: Array<{ label: string; value: number }>;
};

type PersistedLayout = {
  interval: IntervalKey;
  range: RangeKey;
  chartStyle: ChartStyle;
  scaleMode: ScaleMode;
  indicators: IndicatorKey[];
  compare: string | null;
  showPatterns: boolean;
  showAutoAnalysis: boolean;
  drawings: DrawingShape[];
};

const INTERVALS: IntervalKey[] = ["5m", "15m", "30m", "1H", "1D", "1W", "1M"];
const RANGES: RangeKey[] = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "ALL"];
const CHART_STYLES: Array<{ key: ChartStyle; label: string }> = [
  { key: "candles", label: "Candles" },
  { key: "heikin", label: "Heikin Ashi" },
  { key: "bars", label: "Bars" },
  { key: "line", label: "Line" },
  { key: "area", label: "Area" },
  { key: "baseline", label: "Baseline" },
];
const INDICATORS: Array<{ key: IndicatorKey; label: string; group: string }> = [
  { key: "sma20", label: "SMA 20", group: "Trend" }, { key: "sma50", label: "SMA 50", group: "Trend" },
  { key: "sma100", label: "SMA 100", group: "Trend" }, { key: "sma200", label: "SMA 200", group: "Trend" },
  { key: "ema20", label: "EMA 20", group: "Trend" }, { key: "ema50", label: "EMA 50", group: "Trend" },
  { key: "bollinger", label: "Bollinger Bands", group: "Trend" }, { key: "vwap20", label: "VWAP 20", group: "Trend" },
  { key: "highLow52", label: "52W High / Low", group: "Levels" }, { key: "pivots", label: "Pivot Points", group: "Levels" },
  { key: "fib", label: "Auto Fibonacci", group: "Levels" },
  { key: "rsi14", label: "RSI 14", group: "Momentum" }, { key: "macd", label: "MACD", group: "Momentum" },
  { key: "stochastic", label: "Stochastic", group: "Momentum" }, { key: "cci20", label: "CCI 20", group: "Momentum" },
  { key: "roc12", label: "ROC 12", group: "Momentum" }, { key: "atr14", label: "ATR 14", group: "Volatility" },
  { key: "obv", label: "OBV", group: "Volume" },
];
const DRAWING_TOOLS: Array<{ key: DrawingTool; label: string; points: number }> = [
  { key: "trendline", label: "Trend", points: 2 }, { key: "ray", label: "Ray", points: 2 },
  { key: "horizontal", label: "H-Line", points: 1 }, { key: "vertical", label: "V-Line", points: 1 },
  { key: "zone", label: "S/R Zone", points: 2 }, { key: "fib", label: "Fib", points: 2 },
  { key: "measure", label: "Measure", points: 2 }, { key: "arrow", label: "Arrow", points: 2 },
  { key: "text", label: "Text", points: 1 }, { key: "eraser", label: "Erase", points: 1 },
];

const DEFAULT_INDICATORS: IndicatorKey[] = ["sma20", "sma50"];
const MAIN_PANE_HEIGHT = 380;
const SUB_PANE_HEIGHT = 125;
const VOLUME_PANE_HEIGHT = 90;

function css(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function rgba(hex: string, alpha: number): string {
  const cleaned = hex.replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(cleaned)) return hex;
  const number = Number.parseInt(cleaned, 16);
  return `rgba(${(number >> 16) & 255},${(number >> 8) & 255},${number & 255},${alpha})`;
}

function targetKey(target: ChartTarget) { return target.kind === "stock" ? target.symbol : "NEPSE"; }
function layoutKey(target: ChartTarget) { return `artha-chart-layout:v2:${targetKey(target)}`; }
function isIntraday(interval: IntervalKey) { return ["5m", "15m", "30m", "1H"].includes(interval); }
function intradayMinutes(interval: IntervalKey) { return interval === "15m" ? 15 : interval === "30m" ? 30 : interval === "1H" ? 60 : 5; }
function historyUrl(target: ChartTarget, range = "ALL") {
  return target.kind === "stock"
    ? `/api/stocks/${encodeURIComponent(target.symbol)}/history?range=${range}`
    : `/api/market/index-history?range=${range}`;
}
function intradayUrl(target: ChartTarget) {
  return target.kind === "stock"
    ? `/api/stocks/${encodeURIComponent(target.symbol)}/intraday-today`
    : `/api/market/index-intraday-today`;
}
function compareUrl(symbol: string) {
  return symbol.toUpperCase() === "NEPSE" ? `/api/market/index-history?range=ALL` : `/api/stocks/${encodeURIComponent(symbol.toUpperCase())}/history?range=ALL`;
}

function mapHistory(rows: HistoryPoint[]): ChartBar[] {
  return rows.map((row) => ({
    time: row.date, open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close), volume: row.volume,
  })).filter((bar) => [bar.open, bar.high, bar.low, bar.close].every(Number.isFinite));
}

function indicatorValueAt(bundle: IndicatorBundle, time: Time, enabled: IndicatorKey[]) {
  const lookups: Array<[IndicatorKey, string, IndicatorPoint[]]> = [
    ["sma20", "SMA20", bundle.sma20], ["sma50", "SMA50", bundle.sma50], ["sma100", "SMA100", bundle.sma100],
    ["sma200", "SMA200", bundle.sma200], ["ema20", "EMA20", bundle.ema20], ["ema50", "EMA50", bundle.ema50],
    ["vwap20", "VWAP20", bundle.vwap20], ["rsi14", "RSI", bundle.rsi14], ["atr14", "ATR", bundle.atr14],
    ["obv", "OBV", bundle.obv], ["cci20", "CCI", bundle.cci20], ["roc12", "ROC", bundle.roc12],
  ];
  const result: Array<{ label: string; value: number }> = [];
  for (const [key, label, points] of lookups) {
    if (!enabled.includes(key)) continue;
    const point = points.find((entry) => entry.time === time);
    if (point) result.push({ label, value: point.value });
  }
  return result.slice(0, 6);
}

function selectNearestShape(
  shapes: DrawingShape[], point: { x: number; y: number }, chart: IChartApi, series: ISeriesApi<"Candlestick"> | ISeriesApi<"Bar"> | ISeriesApi<"Line"> | ISeriesApi<"Area"> | ISeriesApi<"Baseline">,
): number | null {
  let best: { id: number; distance: number } | null = null;
  for (const shape of shapes) {
    const x1 = chart.timeScale().timeToCoordinate(shape.p1.time);
    const y1 = series.priceToCoordinate(shape.p1.price);
    if (x1 === null || y1 === null) continue;
    let distance = Math.hypot(point.x - x1, point.y - y1);
    if ("p2" in shape) {
      const x2 = chart.timeScale().timeToCoordinate(shape.p2.time);
      const y2 = series.priceToCoordinate(shape.p2.price);
      if (x2 !== null && y2 !== null) {
        const dx = x2 - x1; const dy = y2 - y1;
        const length2 = dx * dx + dy * dy || 1;
        const t = Math.max(0, Math.min(1, ((point.x - x1) * dx + (point.y - y1) * dy) / length2));
        distance = Math.min(distance, Math.hypot(point.x - (x1 + t * dx), point.y - (y1 + t * dy)));
      }
    }
    if (!best || distance < best.distance) best = { id: shape.id, distance };
  }
  return best && best.distance <= 18 ? best.id : null;
}

export function TimeframeChart({ target, artha }: { target: ChartTarget; artha?: ArthaChartSnapshot | null }) {
  const { theme } = useTheme();
  const [interval, setInterval] = useState<IntervalKey>("1D");
  const [range, setRange] = useState<RangeKey>("6M");
  const [chartStyle, setChartStyle] = useState<ChartStyle>("candles");
  const [scaleMode, setScaleMode] = useState<ScaleMode>("linear");
  const [indicators, setIndicators] = useState<IndicatorKey[]>(DEFAULT_INDICATORS);
  const [showPatterns, setShowPatterns] = useState(false);
  const [showAutoAnalysis, setShowAutoAnalysis] = useState(true);
  const [compare, setCompare] = useState<string | null>(null);
  const [compareDraft, setCompareDraft] = useState("");
  const [indicatorMenu, setIndicatorMenu] = useState(false);
  const [compareMenu, setCompareMenu] = useState(false);
  const [fullScreen, setFullScreen] = useState(false);
  const [rawBars, setRawBars] = useState<ChartBar[]>([]);
  const [compareBars, setCompareBars] = useState<ChartBar[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [hover, setHover] = useState<HoverSnapshot | null>(null);
  const [drawingTool, setDrawingTool] = useState<DrawingTool>("none");
  const [drawings, setDrawings] = useState<DrawingShape[]>([]);
  const [undoStack, setUndoStack] = useState<DrawingShape[][]>([]);
  const [redoStack, setRedoStack] = useState<DrawingShape[][]>([]);
  const [replayIndex, setReplayIndex] = useState<number | null>(null);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [layoutLoaded, setLayoutLoaded] = useState(false);

  const shellRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const drawingRef = useRef<DrawingPrimitive | null>(null);
  const pendingRef = useRef<DrawingPoint | null>(null);
  const toolRef = useRef<DrawingTool>("none");
  const drawingsRef = useRef<DrawingShape[]>([]);
  const nextIdRef = useRef(1);

  useEffect(() => { toolRef.current = drawingTool; }, [drawingTool]);
  useEffect(() => {
    try {
      const saved = localStorage.getItem(layoutKey(target));
      if (saved) {
        const layout = JSON.parse(saved) as Partial<PersistedLayout>;
        if (layout.interval && INTERVALS.includes(layout.interval)) setInterval(layout.interval);
        if (layout.range && RANGES.includes(layout.range)) setRange(layout.range);
        if (layout.chartStyle && CHART_STYLES.some((entry) => entry.key === layout.chartStyle)) setChartStyle(layout.chartStyle);
        if (layout.scaleMode) setScaleMode(layout.scaleMode);
        if (Array.isArray(layout.indicators)) setIndicators(layout.indicators.filter((key): key is IndicatorKey => INDICATORS.some((entry) => entry.key === key)));
        if (typeof layout.compare === "string" || layout.compare === null) setCompare(layout.compare ?? null);
        if (typeof layout.showPatterns === "boolean") setShowPatterns(layout.showPatterns);
        if (typeof layout.showAutoAnalysis === "boolean") setShowAutoAnalysis(layout.showAutoAnalysis);
        if (Array.isArray(layout.drawings)) {
          setDrawings(layout.drawings);
          nextIdRef.current = Math.max(1, ...layout.drawings.map((shape) => shape.id + 1));
        }
      }
    } catch { /* ignore corrupt local layout */ }
    setLayoutLoaded(true);
  }, [target]);

  useEffect(() => {
    if (!layoutLoaded) return;
    const layout: PersistedLayout = { interval, range, chartStyle, scaleMode, indicators, compare, showPatterns, showAutoAnalysis, drawings };
    localStorage.setItem(layoutKey(target), JSON.stringify(layout));
  }, [layoutLoaded, target, interval, range, chartStyle, scaleMode, indicators, compare, showPatterns, showAutoAnalysis, drawings]);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    const url = isIntraday(interval) ? intradayUrl(target) : historyUrl(target, "ALL");
    fetch(url, { cache: "no-store" })
      .then((response) => { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
      .then((payload) => {
        if (cancelled) return;
        if (isIntraday(interval)) {
          const data = payload as IntradayResponse;
          const points = (data.points ?? []).map((point) => ({ time: Number(point.time), price: Number(point.price), volume: point.volume }));
          setRawBars(aggregateIntraday(points, intradayMinutes(interval)));
        } else {
          setRawBars(mapHistory(payload as HistoryPoint[]));
        }
        setStatus("ready");
      })
      .catch(() => { if (!cancelled) { setRawBars([]); setStatus("error"); } });
    return () => { cancelled = true; };
  }, [target, interval]);

  useEffect(() => {
    if (!compare || isIntraday(interval)) { setCompareBars([]); return; }
    let cancelled = false;
    fetch(compareUrl(compare), { cache: "no-store" })
      .then((response) => { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
      .then((payload) => { if (!cancelled) setCompareBars(mapHistory(payload as HistoryPoint[])); })
      .catch(() => { if (!cancelled) setCompareBars([]); });
    return () => { cancelled = true; };
  }, [compare, interval]);

  const intervalBars = useMemo(() => {
    if (isIntraday(interval)) return rawBars;
    if (interval === "1W" || interval === "1M") return resampleBars(rawBars, interval);
    return rawBars;
  }, [rawBars, interval]);

  const rangedBars = useMemo(() => isIntraday(interval) ? intervalBars : filterBarsByRange(intervalBars, range), [intervalBars, interval, range]);
  const replayBars = useMemo(() => replayIndex === null ? rangedBars : rangedBars.slice(0, Math.max(1, Math.min(replayIndex + 1, rangedBars.length))), [rangedBars, replayIndex]);
  const analysis = useMemo(() => calculateIndicators(replayBars), [replayBars]);
  const displayBars = useMemo(() => chartStyle === "heikin" ? heikinAshi(replayBars) : replayBars, [chartStyle, replayBars]);
  const levels = useMemo(() => autoLevels(replayBars), [replayBars]);
  const patterns = useMemo(() => showPatterns ? detectPatterns(replayBars) : [], [showPatterns, replayBars]);
  const autoDrawingShapes = useMemo<DrawingShape[]>(() => {
    if (!showAutoAnalysis) return [];
    const shapes: DrawingShape[] = [];
    if (levels.lowSwing.length === 2 && levels.lowSwing[1].low > levels.lowSwing[0].low) shapes.push({ id: -101, kind: "ray", p1: { time: levels.lowSwing[0].time as Time, price: levels.lowSwing[0].low }, p2: { time: levels.lowSwing[1].time as Time, price: levels.lowSwing[1].low } });
    if (levels.highSwing.length === 2 && levels.highSwing[1].high < levels.highSwing[0].high) shapes.push({ id: -102, kind: "ray", p1: { time: levels.highSwing[0].time as Time, price: levels.highSwing[0].high }, p2: { time: levels.highSwing[1].time as Time, price: levels.highSwing[1].high } });
    return shapes;
  }, [levels, showAutoAnalysis]);

  useEffect(() => {
    drawingsRef.current = drawings;
    drawingRef.current?.setShapes([...drawings, ...autoDrawingShapes]);
  }, [drawings, autoDrawingShapes]);

  useEffect(() => {
    if (!replayPlaying || replayIndex === null) return;
    const id = window.setInterval(() => {
      setReplayIndex((current) => {
        if (current === null || current >= rangedBars.length - 1) { setReplayPlaying(false); return current; }
        return current + 1;
      });
    }, 700);
    return () => window.clearInterval(id);
  }, [replayPlaying, replayIndex, rangedBars.length]);

  const visibleCompare = useMemo(() => {
    if (!compare || compareBars.length === 0 || replayBars.length === 0) return [];
    let bars = interval === "1W" || interval === "1M" ? resampleBars(compareBars, interval) : compareBars;
    bars = filterBarsByRange(bars, range);
    if (replayIndex !== null && replayBars.length) {
      const end = replayBars[replayBars.length - 1].time;
      bars = bars.filter((bar) => String(bar.time) <= String(end));
    }
    return bars;
  }, [compare, compareBars, interval, range, replayIndex, replayBars]);

  const panelCount = ["rsi14", "macd", "stochastic", "atr14", "obv", "cci20", "roc12"].filter((key) => indicators.includes(key as IndicatorKey)).length;
  const totalHeight = MAIN_PANE_HEIGHT + (replayBars.some((bar) => bar.volume !== undefined) ? VOLUME_PANE_HEIGHT : 0) + panelCount * SUB_PANE_HEIGHT;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || status !== "ready" || displayBars.length === 0) return;

    const border = css("--color-border", "#2a2d33");
    const text = css("--color-text-secondary", "#9a9ca3");
    const accent = css("--color-accent-primary-light", "#a9c29a");
    const accentStrong = css("--color-accent-primary", "#4f5f45");
    const up = css("--color-success-text", "#16a34a");
    const down = css("--color-danger-text", "#dc2626");
    const warning = css("--color-warning-text", "#d97706");
    const card = css("--color-card", "#1b1d21");

    const chart = createChart(container, {
      width: container.clientWidth,
      height: totalHeight,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: text, attributionLogo: true, panes: { enableResize: true, separatorColor: border, separatorHoverColor: accentStrong } },
      grid: { vertLines: { color: border }, horzLines: { color: border } },
      rightPriceScale: { borderColor: border, mode: scaleMode === "log" ? PriceScaleMode.Logarithmic : scaleMode === "percent" ? PriceScaleMode.Percentage : PriceScaleMode.Normal },
      timeScale: { borderColor: border, timeVisible: isIntraday(interval), secondsVisible: false, barSpacing: isIntraday(interval) ? 9 : 7, rightOffset: 4 },
      crosshair: { vertLine: { color: text, labelBackgroundColor: accentStrong }, horzLine: { color: text, labelBackgroundColor: accentStrong } },
    });
    chartRef.current = chart;

    type MainSeries = ISeriesApi<"Candlestick"> | ISeriesApi<"Bar"> | ISeriesApi<"Line"> | ISeriesApi<"Area"> | ISeriesApi<"Baseline">;
    let mainSeries: MainSeries;
    const ohlc = displayBars.map((bar) => ({ time: bar.time as Time, open: bar.open, high: bar.high, low: bar.low, close: bar.close }));
    const single = displayBars.map((bar) => ({ time: bar.time as Time, value: bar.close }));
    if (chartStyle === "bars") {
      const series = chart.addSeries(BarSeries, { upColor: up, downColor: down, thinBars: true }); series.setData(ohlc); mainSeries = series;
    } else if (chartStyle === "line") {
      const series = chart.addSeries(LineSeries, { color: accent, lineWidth: 2 }); series.setData(single); mainSeries = series;
    } else if (chartStyle === "area") {
      const series = chart.addSeries(AreaSeries, { lineColor: accent, topColor: rgba(accent, 0.35), bottomColor: rgba(accent, 0.02), lineWidth: 2 }); series.setData(single); mainSeries = series;
    } else if (chartStyle === "baseline") {
      const base = displayBars[0]?.close ?? 0;
      const series = chart.addSeries(BaselineSeries, { baseValue: { type: "price", price: base }, topLineColor: up, bottomLineColor: down, topFillColor1: rgba(up, 0.25), topFillColor2: rgba(up, 0.02), bottomFillColor1: rgba(down, 0.02), bottomFillColor2: rgba(down, 0.25), lineWidth: 2 });
      series.setData(single); mainSeries = series;
    } else {
      const series = chart.addSeries(CandlestickSeries, { upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down });
      series.setData(ohlc); mainSeries = series;
    }

    const drawing = new DrawingPrimitive();
    drawing.setColors(accentStrong, rgba(accentStrong, 0.14), css("--color-text-primary", "#fff"), rgba(card, 0.93));
    mainSeries.attachPrimitive(drawing);
    drawing.setShapes(drawingsRef.current);
    drawingRef.current = drawing;

    const addOverlay = (points: IndicatorPoint[], color: string, width: 1 | 2 | 3 = 1) => {
      const series = chart.addSeries(LineSeries, { color, lineWidth: width, priceLineVisible: false, lastValueVisible: false });
      series.setData(points.map((point) => ({ time: point.time as Time, value: point.value })));
      return series;
    };
    const palette = [accent, warning, "#60a5fa", "#c084fc", "#f472b6", "#22d3ee", "#fb7185", "#a3e635"];
    if (indicators.includes("sma20")) addOverlay(analysis.sma20, palette[0], 2);
    if (indicators.includes("sma50")) addOverlay(analysis.sma50, palette[1], 2);
    if (indicators.includes("sma100")) addOverlay(analysis.sma100, palette[2], 1);
    if (indicators.includes("sma200")) addOverlay(analysis.sma200, palette[3], 2);
    if (indicators.includes("ema20")) addOverlay(analysis.ema20, palette[4], 1);
    if (indicators.includes("ema50")) addOverlay(analysis.ema50, palette[5], 1);
    if (indicators.includes("vwap20")) addOverlay(analysis.vwap20, palette[6], 2);
    if (indicators.includes("bollinger")) { addOverlay(analysis.bollingerUpper, palette[2]); addOverlay(analysis.bollingerMiddle, rgba(palette[2], 0.55)); addOverlay(analysis.bollingerLower, palette[2]); }
    if (indicators.includes("highLow52")) { addOverlay(analysis.high52w, down); addOverlay(analysis.low52w, up); }
    if (indicators.includes("pivots")) { addOverlay(analysis.pivot, accent); addOverlay(analysis.pivotR1, down); addOverlay(analysis.pivotS1, up); }
    if (indicators.includes("fib")) { [analysis.fib236, analysis.fib382, analysis.fib50, analysis.fib618, analysis.fib786].forEach((points, index) => addOverlay(points, palette[index + 1])); }

    let paneIndex = 1;
    if (displayBars.some((bar) => bar.volume !== undefined)) {
      const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, paneIndex);
      volume.setData(displayBars.map((bar) => ({ time: bar.time as Time, value: bar.volume ?? 0, color: bar.close >= bar.open ? rgba(up, 0.55) : rgba(down, 0.55) })));
      chart.panes()[paneIndex]?.setHeight(VOLUME_PANE_HEIGHT); paneIndex += 1;
    }
    const addPaneLine = (points: IndicatorPoint[], color: string, pane: number) => {
      const series = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: true }, pane);
      series.setData(points.map((point) => ({ time: point.time as Time, value: point.value }))); return series;
    };
    if (indicators.includes("rsi14")) {
      addPaneLine(analysis.rsi14, palette[0], paneIndex);
      const ghost = chart.addSeries(LineSeries, { color: rgba(text, 0.01), priceLineVisible: false, lastValueVisible: false }, paneIndex);
      ghost.setData([{ time: displayBars[0].time as Time, value: 30 }, { time: displayBars[displayBars.length - 1].time as Time, value: 70 }]);
      ghost.createPriceLine({ price: 70, color: rgba(down, 0.55), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "70" });
      ghost.createPriceLine({ price: 30, color: rgba(up, 0.55), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "30" });
      chart.panes()[paneIndex]?.setHeight(SUB_PANE_HEIGHT); paneIndex += 1;
    }
    if (indicators.includes("macd")) {
      addPaneLine(analysis.macd, palette[2], paneIndex); addPaneLine(analysis.macdSignal, palette[1], paneIndex);
      const histogram = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, paneIndex);
      histogram.setData(analysis.macdHistogram.map((point) => ({ time: point.time as Time, value: point.value, color: point.value >= 0 ? rgba(up, 0.65) : rgba(down, 0.65) })));
      chart.panes()[paneIndex]?.setHeight(SUB_PANE_HEIGHT); paneIndex += 1;
    }
    if (indicators.includes("stochastic")) { addPaneLine(analysis.stochasticK, palette[2], paneIndex); addPaneLine(analysis.stochasticD, palette[1], paneIndex); chart.panes()[paneIndex]?.setHeight(SUB_PANE_HEIGHT); paneIndex += 1; }
    for (const [key, points, color] of [["atr14", analysis.atr14, palette[6]], ["obv", analysis.obv, palette[5]], ["cci20", analysis.cci20, palette[3]], ["roc12", analysis.roc12, palette[4]]] as Array<[IndicatorKey, IndicatorPoint[], string]>) {
      if (indicators.includes(key)) { addPaneLine(points, color, paneIndex); chart.panes()[paneIndex]?.setHeight(SUB_PANE_HEIGHT); paneIndex += 1; }
    }

    if (showAutoAnalysis) {
      if (levels.support !== null) mainSeries.createPriceLine({ price: levels.support, color: rgba(up, 0.75), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "Auto support" });
      if (levels.resistance !== null) mainSeries.createPriceLine({ price: levels.resistance, color: rgba(down, 0.75), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "Auto resistance" });
      drawing.setShapes([...drawingsRef.current, ...autoDrawingShapes]);
    }

    const markerItems: SeriesMarker<Time>[] = [];
    if (artha?.signals?.length) {
      markerItems.push(...artha.signals
        .filter((signal) => signal.entryDate && replayBars.some((bar) => String(bar.time) === signal.entryDate))
        .slice(-12)
        .map((signal) => ({
          time: signal.entryDate as Time,
          position: signal.status.toLowerCase().includes("win") ? "belowBar" as const : "aboveBar" as const,
          color: signal.status.toLowerCase().includes("win") ? up : warning,
          shape: signal.status.toLowerCase().includes("win") ? "arrowUp" as const : "square" as const,
          text: `Artha: ${signal.signalName}`,
        })));
    }
    if (patterns.length > 0) {
      markerItems.push(...patterns.slice(-80).map((marker) => ({
        time: marker.time as Time,
        position: marker.direction === "bearish" ? "aboveBar" as const : "belowBar" as const,
        color: marker.direction === "bearish" ? down : marker.direction === "bullish" ? up : warning,
        shape: marker.direction === "bearish" ? "arrowDown" as const : marker.direction === "bullish" ? "arrowUp" as const : "circle" as const,
        text: marker.label,
      })));
    }
    if (markerItems.length) {
      markerItems.sort((a, b) => String(a.time).localeCompare(String(b.time)));
      const markerAnchor = chart.addSeries(LineSeries, { color: "transparent", lineVisible: false, priceLineVisible: false, lastValueVisible: false });
      markerAnchor.setData(replayBars.map((bar) => ({ time: bar.time as Time, value: bar.close })));
      createSeriesMarkers(markerAnchor, markerItems);
    }

    if (compare && visibleCompare.length > 0 && replayBars.length > 0) {
      const targetPct = normalizeToPercent(replayBars);
      const comparePct = normalizeToPercent(visibleCompare);
      const targetSeries = chart.addSeries(LineSeries, { color: accent, lineWidth: 2, priceScaleId: "compare", priceLineVisible: false, lastValueVisible: false });
      const compareSeries = chart.addSeries(LineSeries, { color: palette[2], lineWidth: 2, priceScaleId: "compare", priceLineVisible: false, lastValueVisible: true, title: compare.toUpperCase() });
      targetSeries.setData(targetPct.map((point) => ({ time: point.time as Time, value: point.value })));
      compareSeries.setData(comparePct.map((point) => ({ time: point.time as Time, value: point.value })));
      chart.priceScale("compare").applyOptions({ mode: PriceScaleMode.Normal, scaleMargins: { top: 0.08, bottom: 0.08 } });
    }

    const pushDrawing = (shape: DrawingShape) => {
      setUndoStack((current) => [...current, drawingsRef.current]); setRedoStack([]); setDrawings((current) => [...current, shape]); pendingRef.current = null; drawing.setDraft(null); setDrawingTool("none");
    };
    const pointFromParam = (param: MouseEventParams): DrawingPoint | null => {
      if (!param.point || param.time === undefined) return null;
      const price = mainSeries.coordinateToPrice(param.point.y); if (price === null) return null;
      return { time: param.time as Time, price };
    };
    const handleClick = (param: MouseEventParams) => {
      const tool = toolRef.current; if (tool === "none") return;
      if (tool === "eraser" && param.point) {
        const id = selectNearestShape(drawingsRef.current, param.point, chart, mainSeries);
        if (id !== null) { setUndoStack((current) => [...current, drawingsRef.current]); setRedoStack([]); setDrawings((current) => current.filter((shape) => shape.id !== id)); }
        setDrawingTool("none"); return;
      }
      const point = pointFromParam(param); if (!point) return;
      if (tool === "text") {
        const label = window.prompt("Annotation text:")?.trim(); if (label) pushDrawing({ id: nextIdRef.current++, kind: "text", p1: point, text: label }); else setDrawingTool("none"); return;
      }
      if (tool === "horizontal") { pushDrawing({ id: nextIdRef.current++, kind: "horizontal", p1: point }); return; }
      if (tool === "vertical") { pushDrawing({ id: nextIdRef.current++, kind: "vertical", p1: point }); return; }
      if (!pendingRef.current) { pendingRef.current = point; return; }
      const kind = tool === "zone" ? "rect" : tool === "trendline" ? "line" : tool;
      if (["line", "ray", "rect", "fib", "measure", "arrow"].includes(kind)) pushDrawing({ id: nextIdRef.current++, kind: kind as "line" | "ray" | "rect" | "fib" | "measure" | "arrow", p1: pendingRef.current, p2: point });
    };
    const handleMove = (param: MouseEventParams) => {
      if (param.time !== undefined) {
        const index = replayBars.findIndex((bar) => bar.time === param.time);
        const bar = index >= 0 ? replayBars[index] : null;
        if (bar) {
          const prev = index > 0 ? replayBars[index - 1].close : null;
          setHover({ time: bar.time as Time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume, changePercent: prev ? ((bar.close - prev) / prev) * 100 : null, indicators: indicatorValueAt(analysis, bar.time as Time, indicators) });
        }
      }
      const tool = toolRef.current;
      if (!pendingRef.current || ["none", "text", "horizontal", "vertical", "eraser"].includes(tool)) return;
      const point = pointFromParam(param); if (!point) return;
      const kind = tool === "zone" ? "rect" : tool === "trendline" ? "line" : tool;
      if (["line", "ray", "rect", "fib", "measure", "arrow"].includes(kind)) drawing.setDraft({ id: -1, kind: kind as "line" | "ray" | "rect" | "fib" | "measure" | "arrow", p1: pendingRef.current, p2: point });
    };
    chart.subscribeClick(handleClick); chart.subscribeCrosshairMove(handleMove);

    const resize = () => chart.resize(container.clientWidth, totalHeight);
    window.addEventListener("resize", resize);
    if (displayBars.length > 120 && !isIntraday(interval)) chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, displayBars.length - 100), to: displayBars.length - 1 }); else chart.timeScale().fitContent();

    return () => {
      window.removeEventListener("resize", resize); chart.unsubscribeClick(handleClick); chart.unsubscribeCrosshairMove(handleMove); chart.remove(); chartRef.current = null; drawingRef.current = null;
    };
  }, [status, displayBars, replayBars, analysis, indicators, chartStyle, scaleMode, interval, totalHeight, compare, visibleCompare, showAutoAnalysis, levels, patterns, theme, artha, autoDrawingShapes, fullScreen]);

  const selectTool = (tool: DrawingTool) => { pendingRef.current = null; drawingRef.current?.setDraft(null); setDrawingTool((current) => current === tool ? "none" : tool); };
  const undo = () => { setUndoStack((stack) => { if (!stack.length) return stack; const previous = stack[stack.length - 1]; setRedoStack((redo) => [...redo, drawingsRef.current]); setDrawings(previous); return stack.slice(0, -1); }); };
  const redo = () => { setRedoStack((stack) => { if (!stack.length) return stack; const next = stack[stack.length - 1]; setUndoStack((undoItems) => [...undoItems, drawingsRef.current]); setDrawings(next); return stack.slice(0, -1); }); };
  const clearDrawings = () => { if (!drawings.length) return; setUndoStack((stack) => [...stack, drawings]); setRedoStack([]); setDrawings([]); };
  const resetLayout = () => { localStorage.removeItem(layoutKey(target)); setInterval("1D"); setRange("6M"); setChartStyle("candles"); setScaleMode("linear"); setIndicators(DEFAULT_INDICATORS); setCompare(null); setShowPatterns(false); setShowAutoAnalysis(true); setDrawings([]); setReplayIndex(null); };
  const exportPng = () => { const canvas = chartRef.current?.takeScreenshot(true, false); if (!canvas) return; const link = document.createElement("a"); link.download = `${targetKey(target)}-chart.png`; link.href = canvas.toDataURL("image/png"); link.click(); };
  const startReplay = () => { if (rangedBars.length < 10) return; setReplayIndex(Math.max(5, Math.floor(rangedBars.length * 0.65))); setReplayPlaying(false); };
  const toggleIndicator = (key: IndicatorKey) => setIndicators((current) => current.includes(key) ? current.filter((entry) => entry !== key) : [...current, key]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { pendingRef.current = null; drawingRef.current?.setDraft(null); setDrawingTool("none"); return; }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const latest = hover ?? (replayBars.length ? (() => { const bar = replayBars[replayBars.length - 1]; const prev = replayBars.length > 1 ? replayBars[replayBars.length - 2].close : null; return { time: bar.time as Time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume, changePercent: prev ? ((bar.close - prev) / prev) * 100 : null, indicators: indicatorValueAt(analysis, bar.time as Time, indicators) } satisfies HoverSnapshot; })() : null);

  return (
    <div ref={shellRef} className={fullScreen ? "fixed inset-0 z-[100] flex flex-col overflow-auto bg-background p-3 sm:p-5" : "flex flex-col gap-3"} data-testid="advanced-chart-workspace">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-2">
        <strong className="mr-1 text-sm text-text-primary">{targetKey(target)}</strong>{artha ? <span className="rounded-md border border-border bg-background px-2 py-1 text-[11px] font-semibold text-accent-text">Artha {artha.score}/100 · {artha.rating.replaceAll("_", " ")} · {artha.confidence}</span> : null}
        <select value={interval} onChange={(event) => { setInterval(event.target.value as IntervalKey); setReplayIndex(null); }} className="rounded-md border border-border bg-background px-2 py-1.5 text-xs text-text-primary" aria-label="Chart interval">{INTERVALS.map((key) => <option key={key}>{key}</option>)}</select>
        <select value={chartStyle} onChange={(event) => setChartStyle(event.target.value as ChartStyle)} className="rounded-md border border-border bg-background px-2 py-1.5 text-xs text-text-primary" aria-label="Chart style">{CHART_STYLES.map((entry) => <option key={entry.key} value={entry.key}>{entry.label}</option>)}</select>
        <select value={scaleMode} onChange={(event) => setScaleMode(event.target.value as ScaleMode)} className="rounded-md border border-border bg-background px-2 py-1.5 text-xs text-text-primary" aria-label="Price scale"><option value="linear">Linear</option><option value="log">Log</option><option value="percent">%</option></select>
        <div className="relative">
          <button type="button" onClick={() => setIndicatorMenu((value) => !value)} className="rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-text-secondary hover:text-text-primary">Indicators ({indicators.length})</button>
          {indicatorMenu && <div className="absolute left-0 top-full z-30 mt-2 max-h-80 w-64 overflow-auto rounded-xl border border-border bg-card p-3 shadow-xl"><p className="mb-2 text-xs font-semibold text-text-primary">Technical indicators</p>{Array.from(new Set(INDICATORS.map((item) => item.group))).map((group) => <div key={group} className="mb-3"><p className="mb-1 text-[10px] uppercase tracking-wider text-text-secondary">{group}</p>{INDICATORS.filter((item) => item.group === group).map((item) => <label key={item.key} className="flex cursor-pointer items-center gap-2 py-1 text-xs text-text-primary"><input type="checkbox" checked={indicators.includes(item.key)} onChange={() => toggleIndicator(item.key)} />{item.label}</label>)}</div>)}</div>}
        </div>
        <div className="relative">
          <button type="button" disabled={isIntraday(interval)} onClick={() => setCompareMenu((value) => !value)} className="rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-text-secondary hover:text-text-primary disabled:opacity-40">Compare{compare ? `: ${compare.toUpperCase()}` : " +"}</button>
          {compareMenu && <div className="absolute left-0 top-full z-30 mt-2 w-64 rounded-xl border border-border bg-card p-3 shadow-xl"><div className="flex gap-2"><input value={compareDraft} onChange={(event) => setCompareDraft(event.target.value)} placeholder="NEPSE or symbol" className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-xs text-text-primary" /><button type="button" onClick={() => { const value = compareDraft.trim().toUpperCase(); setCompare(value || null); setCompareMenu(false); }} className="rounded-md bg-accent-primary px-2 py-1 text-xs text-white">Add</button></div><button type="button" onClick={() => { setCompare("NEPSE"); setCompareMenu(false); }} className="mt-2 text-xs text-accent-text">Compare with NEPSE</button>{compare && <button type="button" onClick={() => { setCompare(null); setCompareMenu(false); }} className="ml-3 text-xs text-danger-text">Remove</button>}</div>}
        </div>
        <button type="button" onClick={() => setShowPatterns((value) => !value)} aria-pressed={showPatterns} className={`rounded-md border px-2.5 py-1.5 text-xs ${showPatterns ? "border-accent-primary bg-accent-primary text-white" : "border-border text-text-secondary"}`}>Patterns</button>
        <button type="button" onClick={() => setShowAutoAnalysis((value) => !value)} aria-pressed={showAutoAnalysis} className={`rounded-md border px-2.5 py-1.5 text-xs ${showAutoAnalysis ? "border-accent-primary bg-accent-primary text-white" : "border-border text-text-secondary"}`}>Auto S/R</button>
        <div className="ml-auto flex flex-wrap gap-1">
          <button type="button" onClick={() => chartRef.current?.timeScale().fitContent()} className="rounded-md border border-border px-2 py-1.5 text-xs text-text-secondary">Fit</button>
          <button type="button" onClick={exportPng} className="rounded-md border border-border px-2 py-1.5 text-xs text-text-secondary">PNG</button>
          <button type="button" onClick={() => setFullScreen((value) => !value)} className="rounded-md border border-border px-2 py-1.5 text-xs text-text-secondary">{fullScreen ? "Exit full" : "Fullscreen"}</button>
        </div>
      </div>

      {latest && <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-xs text-text-secondary"><span className="font-medium text-text-primary">{String(latest.time)}</span><span>O <b className="text-text-primary">{latest.open.toFixed(2)}</b></span><span>H <b className="text-text-primary">{latest.high.toFixed(2)}</b></span><span>L <b className="text-text-primary">{latest.low.toFixed(2)}</b></span><span>C <b className="text-text-primary">{latest.close.toFixed(2)}</b></span>{latest.changePercent !== null && <span className={latest.changePercent >= 0 ? "text-success-text" : "text-danger-text"}>{latest.changePercent >= 0 ? "+" : ""}{latest.changePercent.toFixed(2)}%</span>}{latest.volume !== undefined && <span>Vol {Math.round(latest.volume).toLocaleString()}</span>}{latest.indicators.map((entry) => <span key={entry.label}>{entry.label} <b className="text-text-primary">{entry.value.toFixed(2)}</b></span>)}</div>}

      <div className="flex flex-wrap items-center gap-1 rounded-lg border border-border bg-card p-1.5" data-testid="drawing-toolbar">
        {DRAWING_TOOLS.map((tool) => <button key={tool.key} type="button" onClick={() => selectTool(tool.key)} aria-pressed={drawingTool === tool.key} className={`rounded-md px-2 py-1 text-xs ${drawingTool === tool.key ? "bg-accent-primary text-white" : "text-text-secondary hover:bg-background hover:text-text-primary"}`}>{tool.label}</button>)}
        <span className="mx-1 h-5 w-px bg-border" />
        <button type="button" disabled={!undoStack.length} onClick={undo} className="rounded-md px-2 py-1 text-xs text-text-secondary disabled:opacity-30">Undo</button>
        <button type="button" disabled={!redoStack.length} onClick={redo} className="rounded-md px-2 py-1 text-xs text-text-secondary disabled:opacity-30">Redo</button>
        <button type="button" onClick={clearDrawings} className="rounded-md px-2 py-1 text-xs text-danger-text">Clear</button>
        {drawingTool !== "none" && <span className="ml-2 text-[11px] text-text-secondary">{DRAWING_TOOLS.find((tool) => tool.key === drawingTool)?.points === 2 ? "Click two chart points" : drawingTool === "eraser" ? "Click near a drawing" : "Click the chart"}</span>}
      </div>

      <div className="rounded-xl border border-border bg-card p-2 sm:p-3">
        {status === "loading" && <div className="flex h-[420px] items-center justify-center text-sm text-text-secondary">Loading chart…</div>}
        {status === "error" && <div className="flex h-[420px] items-center justify-center text-sm text-text-secondary">Chart data is temporarily unavailable.</div>}
        {status === "ready" && displayBars.length === 0 && <div className="flex h-[420px] items-center justify-center text-sm text-text-secondary">No data is available for this interval.</div>}
        <div ref={containerRef} className={status === "ready" && displayBars.length ? "w-full" : "hidden"} data-testid="timeframe-chart" />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex flex-wrap items-center gap-0.5 rounded-full border border-border bg-card p-0.5" data-testid="timeframe-selector">{RANGES.map((key) => <button key={key} type="button" disabled={isIntraday(interval)} onClick={() => { setRange(key); setReplayIndex(null); }} aria-pressed={range === key} className={`rounded-full px-2.5 py-1 text-xs font-medium disabled:opacity-30 ${range === key ? "bg-accent-primary text-white" : "text-text-secondary hover:text-text-primary"}`}>{key}</button>)}</div>
        <div className="ml-auto flex items-center gap-1">
          {replayIndex === null ? <button type="button" disabled={isIntraday(interval) || rangedBars.length < 10} onClick={startReplay} className="rounded-md border border-border px-2.5 py-1 text-xs text-text-secondary disabled:opacity-30">Replay</button> : <><button type="button" onClick={() => setReplayIndex((value) => value === null ? null : Math.max(1, value - 1))} className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary">−1</button><button type="button" onClick={() => setReplayPlaying((value) => !value)} className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary">{replayPlaying ? "Pause" : "Play"}</button><button type="button" onClick={() => setReplayIndex((value) => value === null ? null : Math.min(rangedBars.length - 1, value + 1))} className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary">+1</button><span className="px-1 text-xs text-warning-text">Future hidden</span><button type="button" onClick={() => { setReplayIndex(null); setReplayPlaying(false); }} className="rounded-md border border-border px-2 py-1 text-xs text-text-secondary">Live</button></>}
          <button type="button" onClick={resetLayout} className="rounded-md border border-border px-2.5 py-1 text-xs text-text-secondary">Reset layout</button>
        </div>
      </div>
    </div>
  );
}
