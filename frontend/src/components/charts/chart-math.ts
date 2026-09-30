export type ChartBar = {
  time: string | number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
};

export type IndicatorPoint = { time: string | number; value: number };

export type IndicatorBundle = {
  sma20: IndicatorPoint[];
  sma50: IndicatorPoint[];
  sma100: IndicatorPoint[];
  sma200: IndicatorPoint[];
  ema20: IndicatorPoint[];
  ema50: IndicatorPoint[];
  bollingerMiddle: IndicatorPoint[];
  bollingerUpper: IndicatorPoint[];
  bollingerLower: IndicatorPoint[];
  vwap20: IndicatorPoint[];
  rsi14: IndicatorPoint[];
  macd: IndicatorPoint[];
  macdSignal: IndicatorPoint[];
  macdHistogram: IndicatorPoint[];
  stochasticK: IndicatorPoint[];
  stochasticD: IndicatorPoint[];
  atr14: IndicatorPoint[];
  obv: IndicatorPoint[];
  cci20: IndicatorPoint[];
  roc12: IndicatorPoint[];
  high52w: IndicatorPoint[];
  low52w: IndicatorPoint[];
  pivot: IndicatorPoint[];
  pivotR1: IndicatorPoint[];
  pivotR2: IndicatorPoint[];
  pivotS1: IndicatorPoint[];
  pivotS2: IndicatorPoint[];
  fib0: IndicatorPoint[];
  fib236: IndicatorPoint[];
  fib382: IndicatorPoint[];
  fib50: IndicatorPoint[];
  fib618: IndicatorPoint[];
  fib786: IndicatorPoint[];
  fib100: IndicatorPoint[];
};

export type PatternMarker = {
  time: string | number;
  direction: "bullish" | "bearish" | "neutral";
  label: string;
};

export type AutoLevels = {
  support: number | null;
  resistance: number | null;
  supportTouches: number;
  resistanceTouches: number;
  lowSwing: ChartBar[];
  highSwing: ChartBar[];
  tolerance: number;
};

function compact(values: Array<number | null>, bars: ChartBar[]): IndicatorPoint[] {
  const result: IndicatorPoint[] = [];
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (value !== null && Number.isFinite(value)) result.push({ time: bars[i].time, value });
  }
  return result;
}

function sma(values: number[], period: number): Array<number | null> {
  const out: Array<number | null> = Array(values.length).fill(null);
  if (period <= 0 || values.length < period) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i += 1) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

function ema(values: number[], period: number): Array<number | null> {
  const out: Array<number | null> = Array(values.length).fill(null);
  if (values.length < period) return out;
  const alpha = 2 / (period + 1);
  let seed = 0;
  for (let i = 0; i < period; i += 1) seed += values[i];
  let previous = seed / period;
  out[period - 1] = previous;
  for (let i = period; i < values.length; i += 1) {
    previous = alpha * values[i] + (1 - alpha) * previous;
    out[i] = previous;
  }
  return out;
}

function rsi(values: number[], period = 14): Array<number | null> {
  const out: Array<number | null> = Array(values.length).fill(null);
  if (values.length < period + 1) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i += 1) {
    const change = values[i] - values[i - 1];
    if (change > 0) gain += change;
    else loss -= change;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  const calc = () => {
    if (avgLoss === 0) return avgGain === 0 ? 50 : 100;
    const rs = avgGain / avgLoss;
    return 100 - 100 / (1 + rs);
  };
  out[period] = calc();
  for (let i = period + 1; i < values.length; i += 1) {
    const change = values[i] - values[i - 1];
    const nextGain = change > 0 ? change : 0;
    const nextLoss = change < 0 ? -change : 0;
    avgGain = (avgGain * (period - 1) + nextGain) / period;
    avgLoss = (avgLoss * (period - 1) + nextLoss) / period;
    out[i] = calc();
  }
  return out;
}

function bollinger(values: number[], period = 20, stdDev = 2) {
  const middle: Array<number | null> = Array(values.length).fill(null);
  const upper: Array<number | null> = Array(values.length).fill(null);
  const lower: Array<number | null> = Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i += 1) {
    const window = values.slice(i - period + 1, i + 1);
    const mean = window.reduce((a, b) => a + b, 0) / period;
    const variance = window.reduce((sum, value) => sum + (value - mean) ** 2, 0) / period;
    const sigma = Math.sqrt(variance);
    middle[i] = mean;
    upper[i] = mean + stdDev * sigma;
    lower[i] = mean - stdDev * sigma;
  }
  return { middle, upper, lower };
}

function macd(values: number[]) {
  const fast = ema(values, 12);
  const slow = ema(values, 26);
  const line: Array<number | null> = Array(values.length).fill(null);
  for (let i = 0; i < values.length; i += 1) {
    if (fast[i] !== null && slow[i] !== null) line[i] = (fast[i] as number) - (slow[i] as number);
  }
  const signal: Array<number | null> = Array(values.length).fill(null);
  const histogram: Array<number | null> = Array(values.length).fill(null);
  const indexes = line.map((value, index) => (value === null ? -1 : index)).filter((index) => index >= 0);
  if (indexes.length >= 9) {
    const seedIndexes = indexes.slice(0, 9);
    let previous = seedIndexes.reduce((sum, index) => sum + (line[index] as number), 0) / 9;
    const seedIndex = seedIndexes[seedIndexes.length - 1];
    signal[seedIndex] = previous;
    histogram[seedIndex] = (line[seedIndex] as number) - previous;
    const alpha = 2 / 10;
    for (const index of indexes.slice(9)) {
      previous = alpha * (line[index] as number) + (1 - alpha) * previous;
      signal[index] = previous;
      histogram[index] = (line[index] as number) - previous;
    }
  }
  return { line, signal, histogram };
}

function stochastic(bars: ChartBar[], kPeriod = 14, dPeriod = 3) {
  const k: Array<number | null> = Array(bars.length).fill(null);
  const d: Array<number | null> = Array(bars.length).fill(null);
  for (let i = kPeriod - 1; i < bars.length; i += 1) {
    const window = bars.slice(i - kPeriod + 1, i + 1);
    const high = Math.max(...window.map((bar) => bar.high));
    const low = Math.min(...window.map((bar) => bar.low));
    k[i] = high === low ? 0 : (100 * (bars[i].close - low)) / (high - low);
  }
  const indexes = k.map((value, index) => (value === null ? -1 : index)).filter((index) => index >= 0);
  for (let position = dPeriod - 1; position < indexes.length; position += 1) {
    const windowIndexes = indexes.slice(position - dPeriod + 1, position + 1);
    d[indexes[position]] = windowIndexes.reduce((sum, index) => sum + (k[index] as number), 0) / dPeriod;
  }
  return { k, d };
}

function atr(bars: ChartBar[], period = 14): Array<number | null> {
  const out: Array<number | null> = Array(bars.length).fill(null);
  if (bars.length < period + 1) return out;
  const tr = Array(bars.length).fill(0);
  for (let i = 1; i < bars.length; i += 1) {
    tr[i] = Math.max(
      bars[i].high - bars[i].low,
      Math.abs(bars[i].high - bars[i - 1].close),
      Math.abs(bars[i].low - bars[i - 1].close),
    );
  }
  let previous = tr.slice(1, period + 1).reduce((a, b) => a + b, 0) / period;
  out[period] = previous;
  for (let i = period + 1; i < bars.length; i += 1) {
    previous = (previous * (period - 1) + tr[i]) / period;
    out[i] = previous;
  }
  return out;
}

function obv(bars: ChartBar[]) {
  const out = Array(bars.length).fill(0);
  for (let i = 1; i < bars.length; i += 1) {
    const volume = bars[i].volume ?? 0;
    if (bars[i].close > bars[i - 1].close) out[i] = out[i - 1] + volume;
    else if (bars[i].close < bars[i - 1].close) out[i] = out[i - 1] - volume;
    else out[i] = out[i - 1];
  }
  return out;
}

function vwap(bars: ChartBar[], period = 20): Array<number | null> {
  const out: Array<number | null> = Array(bars.length).fill(null);
  for (let i = period - 1; i < bars.length; i += 1) {
    let numerator = 0;
    let denominator = 0;
    for (let j = i - period + 1; j <= i; j += 1) {
      const volume = bars[j].volume ?? 0;
      numerator += ((bars[j].high + bars[j].low + bars[j].close) / 3) * volume;
      denominator += volume;
    }
    out[i] = denominator === 0 ? null : numerator / denominator;
  }
  return out;
}

function cci(bars: ChartBar[], period = 20): Array<number | null> {
  const out: Array<number | null> = Array(bars.length).fill(null);
  const tp = bars.map((bar) => (bar.high + bar.low + bar.close) / 3);
  for (let i = period - 1; i < bars.length; i += 1) {
    const window = tp.slice(i - period + 1, i + 1);
    const mean = window.reduce((a, b) => a + b, 0) / period;
    const meanDeviation = window.reduce((sum, value) => sum + Math.abs(value - mean), 0) / period;
    out[i] = meanDeviation === 0 ? null : (tp[i] - mean) / (0.015 * meanDeviation);
  }
  return out;
}

function roc(values: number[], period = 12): Array<number | null> {
  const out: Array<number | null> = Array(values.length).fill(null);
  for (let i = period; i < values.length; i += 1) {
    const base = values[i - period];
    out[i] = base === 0 ? null : (100 * (values[i] - base)) / base;
  }
  return out;
}

function highLow(bars: ChartBar[], window = 252) {
  const high: Array<number | null> = Array(bars.length).fill(null);
  const low: Array<number | null> = Array(bars.length).fill(null);
  for (let i = window - 1; i < bars.length; i += 1) {
    const slice = bars.slice(i - window + 1, i + 1);
    high[i] = Math.max(...slice.map((bar) => bar.high));
    low[i] = Math.min(...slice.map((bar) => bar.low));
  }
  return { high, low };
}

function pivots(bars: ChartBar[]) {
  const p: Array<number | null> = Array(bars.length).fill(null);
  const r1: Array<number | null> = Array(bars.length).fill(null);
  const r2: Array<number | null> = Array(bars.length).fill(null);
  const s1: Array<number | null> = Array(bars.length).fill(null);
  const s2: Array<number | null> = Array(bars.length).fill(null);
  for (let i = 1; i < bars.length; i += 1) {
    const prev = bars[i - 1];
    const pivot = (prev.high + prev.low + prev.close) / 3;
    p[i] = pivot;
    r1[i] = 2 * pivot - prev.low;
    s1[i] = 2 * pivot - prev.high;
    r2[i] = pivot + (prev.high - prev.low);
    s2[i] = pivot - (prev.high - prev.low);
  }
  return { p, r1, r2, s1, s2 };
}

function fibonacci(bars: ChartBar[], lookback = 20) {
  const ratios = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  const levels = ratios.map(() => Array<number | null>(bars.length).fill(null));
  for (let i = lookback - 1; i < bars.length; i += 1) {
    const slice = bars.slice(i - lookback + 1, i + 1);
    const high = Math.max(...slice.map((bar) => bar.high));
    const low = Math.min(...slice.map((bar) => bar.low));
    const span = high - low;
    ratios.forEach((ratio, index) => { levels[index][i] = high - ratio * span; });
  }
  return levels;
}

export function calculateIndicators(bars: ChartBar[]): IndicatorBundle {
  const closes = bars.map((bar) => bar.close);
  const bb = bollinger(closes);
  const macdData = macd(closes);
  const stoch = stochastic(bars);
  const highLowData = highLow(bars);
  const pivotData = pivots(bars);
  const fib = fibonacci(bars);
  return {
    sma20: compact(sma(closes, 20), bars),
    sma50: compact(sma(closes, 50), bars),
    sma100: compact(sma(closes, 100), bars),
    sma200: compact(sma(closes, 200), bars),
    ema20: compact(ema(closes, 20), bars),
    ema50: compact(ema(closes, 50), bars),
    bollingerMiddle: compact(bb.middle, bars),
    bollingerUpper: compact(bb.upper, bars),
    bollingerLower: compact(bb.lower, bars),
    vwap20: compact(vwap(bars, 20), bars),
    rsi14: compact(rsi(closes, 14), bars),
    macd: compact(macdData.line, bars),
    macdSignal: compact(macdData.signal, bars),
    macdHistogram: compact(macdData.histogram, bars),
    stochasticK: compact(stoch.k, bars),
    stochasticD: compact(stoch.d, bars),
    atr14: compact(atr(bars, 14), bars),
    obv: compact(obv(bars), bars),
    cci20: compact(cci(bars, 20), bars),
    roc12: compact(roc(closes, 12), bars),
    high52w: compact(highLowData.high, bars),
    low52w: compact(highLowData.low, bars),
    pivot: compact(pivotData.p, bars),
    pivotR1: compact(pivotData.r1, bars),
    pivotR2: compact(pivotData.r2, bars),
    pivotS1: compact(pivotData.s1, bars),
    pivotS2: compact(pivotData.s2, bars),
    fib0: compact(fib[0], bars), fib236: compact(fib[1], bars), fib382: compact(fib[2], bars),
    fib50: compact(fib[3], bars), fib618: compact(fib[4], bars), fib786: compact(fib[5], bars), fib100: compact(fib[6], bars),
  };
}

export function heikinAshi(bars: ChartBar[]): ChartBar[] {
  if (bars.length === 0) return [];
  const out: ChartBar[] = [];
  for (let i = 0; i < bars.length; i += 1) {
    const bar = bars[i];
    const close = (bar.open + bar.high + bar.low + bar.close) / 4;
    const open = i === 0 ? (bar.open + bar.close) / 2 : (out[i - 1].open + out[i - 1].close) / 2;
    out.push({ ...bar, open, close, high: Math.max(bar.high, open, close), low: Math.min(bar.low, open, close) });
  }
  return out;
}

export function resampleBars(bars: ChartBar[], mode: "1W" | "1M"): ChartBar[] {
  const ordered = [...bars].sort((a, b) => String(a.time).localeCompare(String(b.time)));
  const groups = new Map<string, ChartBar[]>();
  const keyFor = (bar: ChartBar) => {
    const date = new Date(typeof bar.time === "number" ? bar.time * 1000 : `${bar.time}T00:00:00Z`);
    if (mode === "1M") return `${date.getUTCFullYear()}-${date.getUTCMonth() + 1}`;
    const day = date.getUTCDay() || 7;
    const thursday = new Date(date);
    thursday.setUTCDate(date.getUTCDate() + 4 - day);
    const yearStart = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 1));
    const week = Math.ceil((((thursday.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);
    return `${thursday.getUTCFullYear()}-${week}`;
  };
  for (const bar of ordered) {
    const key = keyFor(bar);
    const group = groups.get(key) ?? [];
    group.push(bar);
    groups.set(key, group);
  }
  return Array.from(groups.values()).map((group) => ({
    time: group[group.length - 1].time,
    open: group[0].open,
    high: Math.max(...group.map((bar) => bar.high)),
    low: Math.min(...group.map((bar) => bar.low)),
    close: group[group.length - 1].close,
    volume: group.reduce((sum, bar) => sum + (bar.volume ?? 0), 0),
  }));
}

const NEPAL_TIME_OFFSET_SECONDS = (5 * 60 + 45) * 60;

export function aggregateIntraday(points: Array<{ time: number; price: number; volume?: number }>, minutes: number): ChartBar[] {
  if (points.length === 0) return [];
  const bucketSeconds = minutes * 60;
  const ordered = [...points].sort((a, b) => a.time - b.time);
  const groups = new Map<number, Array<{ time: number; price: number; volume?: number }>>();
  for (const point of ordered) {
    // Anchor buckets to Nepal local clock rather than UTC. This matters for
    // 30m and 1H bars because Nepal's UTC offset is +05:45.
    const bucket = Math.floor((point.time + NEPAL_TIME_OFFSET_SECONDS) / bucketSeconds) * bucketSeconds - NEPAL_TIME_OFFSET_SECONDS;
    const group = groups.get(bucket) ?? [];
    group.push(point);
    groups.set(bucket, group);
  }
  let previousCumulativeVolume = 0;
  return Array.from(groups.entries()).sort((a, b) => a[0] - b[0]).map(([time, group]) => {
    group.sort((a, b) => a.time - b.time);
    const last = group[group.length - 1];
    const cumulativeVolume = last.volume ?? previousCumulativeVolume;
    const volume = Math.max(0, cumulativeVolume - previousCumulativeVolume);
    previousCumulativeVolume = cumulativeVolume;
    return {
      time,
      open: group[0].price,
      high: Math.max(...group.map((point) => point.price)),
      low: Math.min(...group.map((point) => point.price)),
      close: last.price,
      volume,
    };
  });
}

export function filterBarsByRange(bars: ChartBar[], range: string): ChartBar[] {
  if (range === "ALL" || bars.length === 0) return bars;
  if (range === "1D") return bars.slice(-1);
  if (range === "5D") return bars.slice(-5);

  const days: Record<string, number> = { "1M": 30, "3M": 91, "6M": 182, "YTD": 0, "1Y": 365, "3Y": 1095, "5Y": 1825 };
  const last = bars[bars.length - 1];
  const lastDate = new Date(typeof last.time === "number" ? last.time * 1000 : `${last.time}T00:00:00Z`);
  let cutoff: Date;
  if (range === "YTD") cutoff = new Date(Date.UTC(lastDate.getUTCFullYear(), 0, 1));
  else cutoff = new Date(lastDate.getTime() - (days[range] ?? 182) * 86400000);
  return bars.filter((bar) => new Date(typeof bar.time === "number" ? bar.time * 1000 : `${bar.time}T00:00:00Z`) >= cutoff);
}

export function normalizeToPercent(bars: ChartBar[]): IndicatorPoint[] {
  if (bars.length === 0 || bars[0].close === 0) return [];
  const base = bars[0].close;
  return bars.map((bar) => ({ time: bar.time, value: ((bar.close / base) - 1) * 100 }));
}

export function detectPatterns(bars: ChartBar[]): PatternMarker[] {
  const markers: PatternMarker[] = [];
  const stats = bars.map((bar) => {
    const bodyTop = Math.max(bar.open, bar.close);
    const bodyBottom = Math.min(bar.open, bar.close);
    const body = bodyTop - bodyBottom;
    const range = bar.high - bar.low;
    return { bodyTop, bodyBottom, body, range, upper: bar.high - bodyTop, lower: bodyBottom - bar.low, bullish: bar.close > bar.open };
  });
  for (let i = 0; i < bars.length; i += 1) {
    const s = stats[i];
    const downTrend = i >= 3 && bars[i - 1].close < bars[i - 3].close;
    const upTrend = i >= 3 && bars[i - 1].close > bars[i - 3].close;
    if (s.range > 0 && s.body <= 0.05 * s.range) markers.push({ time: bars[i].time, direction: "neutral", label: "Doji" });
    if (downTrend && s.range > 0 && s.body > 0 && s.lower >= 2 * s.body && s.upper <= Math.max(s.body, 0.12 * s.range)) {
      markers.push({ time: bars[i].time, direction: "bullish", label: "Hammer" });
    }
    if (upTrend && s.range > 0 && s.body > 0 && s.upper >= 2 * s.body && s.lower <= Math.max(s.body, 0.12 * s.range)) {
      markers.push({ time: bars[i].time, direction: "bearish", label: "Shooting Star" });
    }
    if (i > 0) {
      const prev = stats[i - 1];
      const contains = s.bodyTop >= prev.bodyTop && s.bodyBottom <= prev.bodyBottom;
      if (contains && !prev.bullish && s.bullish) markers.push({ time: bars[i].time, direction: "bullish", label: "Bull Engulf" });
      if (contains && prev.bullish && !s.bullish) markers.push({ time: bars[i].time, direction: "bearish", label: "Bear Engulf" });
      const contained = s.bodyTop <= prev.bodyTop && s.bodyBottom >= prev.bodyBottom && prev.body > 0 && s.body <= 0.5 * prev.body;
      if (downTrend && contained && !prev.bullish && s.bullish) markers.push({ time: bars[i].time, direction: "bullish", label: "Bull Harami" });
      if (upTrend && contained && prev.bullish && !s.bullish) markers.push({ time: bars[i].time, direction: "bearish", label: "Bear Harami" });
    }
  }
  return markers;
}

type PivotPoint = { index: number; price: number; bar: ChartBar };
type PriceCluster = { price: number; touches: number; latestIndex: number; points: PivotPoint[] };

function typicalTolerance(bars: ChartBar[]): number {
  const recent = bars.slice(-Math.min(20, bars.length));
  if (recent.length === 0) return 0;
  const ranges = recent.map((bar, index) => {
    if (index === 0) return bar.high - bar.low;
    const prevClose = recent[index - 1].close;
    return Math.max(bar.high - bar.low, Math.abs(bar.high - prevClose), Math.abs(bar.low - prevClose));
  });
  const averageTrueRange = ranges.reduce((sum, value) => sum + value, 0) / ranges.length;
  const lastClose = recent[recent.length - 1].close;
  return Math.max(lastClose * 0.006, averageTrueRange * 0.45);
}

function findPivots(bars: ChartBar[], side: "low" | "high"): PivotPoint[] {
  const window = bars.length >= 120 ? 3 : 2;
  const points: PivotPoint[] = [];
  for (let i = window; i < bars.length - window; i += 1) {
    const value = side === "low" ? bars[i].low : bars[i].high;
    let extreme = true;
    for (let j = i - window; j <= i + window; j += 1) {
      if (j === i) continue;
      const candidate = side === "low" ? bars[j].low : bars[j].high;
      if (side === "low" ? candidate < value : candidate > value) {
        extreme = false;
        break;
      }
    }
    if (!extreme) continue;
    const previous = points[points.length - 1];
    if (previous && i - previous.index <= window) {
      const replace = side === "low" ? value < previous.price : value > previous.price;
      if (replace) points[points.length - 1] = { index: i, price: value, bar: bars[i] };
      continue;
    }
    points.push({ index: i, price: value, bar: bars[i] });
  }
  return points;
}

function clusterPivots(points: PivotPoint[], tolerance: number): PriceCluster[] {
  const clusters: PriceCluster[] = [];
  for (const point of points) {
    let bestIndex = -1;
    let bestDistance = Number.POSITIVE_INFINITY;
    clusters.forEach((cluster, index) => {
      const distance = Math.abs(cluster.price - point.price);
      if (distance <= tolerance && distance < bestDistance) {
        bestIndex = index;
        bestDistance = distance;
      }
    });
    if (bestIndex === -1) {
      clusters.push({ price: point.price, touches: 1, latestIndex: point.index, points: [point] });
      continue;
    }
    const cluster = clusters[bestIndex];
    cluster.points.push(point);
    cluster.touches += 1;
    cluster.latestIndex = Math.max(cluster.latestIndex, point.index);
    cluster.price = cluster.points.reduce((sum, item) => sum + item.price, 0) / cluster.points.length;
  }
  return clusters;
}

function pickLevel(clusters: PriceCluster[], lastClose: number, side: "support" | "resistance", tolerance: number, barCount: number): PriceCluster | null {
  const candidates = clusters.filter((cluster) => {
    if (cluster.touches < 2) return false;
    return side === "support" ? cluster.price <= lastClose + tolerance * 0.25 : cluster.price >= lastClose - tolerance * 0.25;
  });
  let best: { cluster: PriceCluster; score: number } | null = null;
  for (const cluster of candidates) {
    const distance = Math.abs(cluster.price - lastClose) / Math.max(lastClose, 1);
    const recency = cluster.latestIndex / Math.max(1, barCount - 1);
    const score = cluster.touches * 6 + recency * 2 - distance * 18;
    if (!best || score > best.score) best = { cluster, score };
  }
  return best?.cluster ?? null;
}

function bestTrendPair(points: PivotPoint[], bars: ChartBar[], side: "low" | "high", tolerance: number): ChartBar[] {
  const recent = points.slice(-10);
  let best: { a: PivotPoint; b: PivotPoint; score: number } | null = null;
  for (let i = 0; i < recent.length - 1; i += 1) {
    for (let j = i + 1; j < recent.length; j += 1) {
      const a = recent[i];
      const b = recent[j];
      const span = b.index - a.index;
      if (span < Math.max(4, Math.floor(bars.length * 0.07))) continue;
      const slope = (b.price - a.price) / span;
      if (Math.abs(slope) * span < tolerance * 0.5) continue;

      let touches = 0;
      let violations = 0;
      for (const point of recent) {
        if (point.index < a.index) continue;
        const expected = a.price + slope * (point.index - a.index);
        const delta = point.price - expected;
        if (Math.abs(delta) <= tolerance) touches += 1;
        if (side === "low" ? delta < -tolerance * 1.2 : delta > tolerance * 1.2) violations += 1;
      }
      if (touches < 2 || violations > 1) continue;
      const recency = b.index / Math.max(1, bars.length - 1);
      const score = touches * 6 + (span / Math.max(1, bars.length)) * 4 + recency * 2 - violations * 8;
      if (!best || score > best.score) best = { a, b, score };
    }
  }
  return best ? [best.a.bar, best.b.bar] : [];
}

export function autoLevels(bars: ChartBar[]): AutoLevels {
  if (bars.length < 12) {
    return { support: null, resistance: null, supportTouches: 0, resistanceTouches: 0, lowSwing: [], highSwing: [], tolerance: 0 };
  }
  const tolerance = typicalTolerance(bars);
  const lowPivots = findPivots(bars, "low");
  const highPivots = findPivots(bars, "high");
  const lastClose = bars[bars.length - 1].close;
  const supportCluster = pickLevel(clusterPivots(lowPivots, tolerance), lastClose, "support", tolerance, bars.length);
  const resistanceCluster = pickLevel(clusterPivots(highPivots, tolerance), lastClose, "resistance", tolerance, bars.length);

  return {
    support: supportCluster?.price ?? null,
    resistance: resistanceCluster?.price ?? null,
    supportTouches: supportCluster?.touches ?? 0,
    resistanceTouches: resistanceCluster?.touches ?? 0,
    lowSwing: bestTrendPair(lowPivots, bars, "low", tolerance),
    highSwing: bestTrendPair(highPivots, bars, "high", tolerance),
    tolerance,
  };
}
