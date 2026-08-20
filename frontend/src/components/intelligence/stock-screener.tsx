"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { MarketIntelligenceStock } from "@/lib/market-data";

type ConfidenceFilter = "all" | MarketIntelligenceStock["confidence_level"];
type TrendFilter = "all" | MarketIntelligenceStock["trend_strength"];
type QualityFilter = "all" | MarketIntelligenceStock["signal_quality"];
type LiquidityFilter = "all" | "high" | "medium" | "low";

function prettyLabel(value: string | null): string {
  if (!value) return "Unavailable";
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function liquidityBand(value: string | null): Exclude<LiquidityFilter, "all"> | "unknown" {
  const normalized = (value ?? "").toLowerCase();
  if (normalized.includes("high") || normalized === "a" || normalized === "tier_a") return "high";
  if (normalized.includes("medium") || normalized === "b" || normalized === "tier_b") return "medium";
  if (normalized.includes("low") || normalized === "c" || normalized === "tier_c") return "low";
  return "unknown";
}

function scoreTone(score: number): string {
  if (score >= 70) return "text-success-text";
  if (score >= 50) return "text-warning-text";
  return "text-danger-text";
}

const CONTROL_CLASS = "h-10 rounded-lg border border-border bg-background px-3 text-sm text-text-primary outline-none focus:border-accent-primary-light";

export function StockScreener({ stocks }: { stocks: MarketIntelligenceStock[] }) {
  const [minimumScore, setMinimumScore] = useState(50);
  const [confidence, setConfidence] = useState<ConfidenceFilter>("all");
  const [liquidity, setLiquidity] = useState<LiquidityFilter>("all");
  const [trend, setTrend] = useState<TrendFilter>("all");
  const [quality, setQuality] = useState<QualityFilter>("all");

  const filtered = useMemo(() => {
    return stocks.filter((stock) => {
      if (stock.artha_score < minimumScore) return false;
      if (confidence !== "all" && stock.confidence_level !== confidence) return false;
      if (liquidity !== "all" && liquidityBand(stock.liquidity_tier) !== liquidity) return false;
      if (trend !== "all" && stock.trend_strength !== trend) return false;
      if (quality !== "all" && stock.signal_quality !== quality) return false;
      return true;
    });
  }, [stocks, minimumScore, confidence, liquidity, trend, quality]);

  const displayed = filtered.slice(0, 75);

  return (
    <section aria-labelledby="stock-screener-title" className="rounded-xl border border-border bg-card p-5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Discovery</p>
          <h2 id="stock-screener-title" className="mt-1 text-lg font-semibold text-text-primary">Artha stock screener</h2>
          <p className="mt-1 text-sm text-text-secondary">Filter the analyzed NEPSE universe by score quality, confidence, liquidity and trend.</p>
        </div>
        <p className="text-xs text-text-secondary">{filtered.length} matches · {stocks.length} analyzed</p>
      </div>

      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <label className="flex flex-col gap-1.5 text-xs text-text-secondary">
          Minimum Artha Score
          <select className={CONTROL_CLASS} value={minimumScore} onChange={(event) => setMinimumScore(Number(event.target.value))}>
            <option value={0}>Any score</option>
            <option value={50}>50+ Neutral</option>
            <option value={60}>60+</option>
            <option value={70}>70+ Positive</option>
            <option value={80}>80+</option>
          </select>
        </label>

        <label className="flex flex-col gap-1.5 text-xs text-text-secondary">
          Confidence
          <select className={CONTROL_CLASS} value={confidence} onChange={(event) => setConfidence(event.target.value as ConfidenceFilter)}>
            <option value="all">All confidence</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>

        <label className="flex flex-col gap-1.5 text-xs text-text-secondary">
          Liquidity
          <select className={CONTROL_CLASS} value={liquidity} onChange={(event) => setLiquidity(event.target.value as LiquidityFilter)}>
            <option value="all">All liquidity</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>

        <label className="flex flex-col gap-1.5 text-xs text-text-secondary">
          Trend strength
          <select className={CONTROL_CLASS} value={trend} onChange={(event) => setTrend(event.target.value as TrendFilter)}>
            <option value="all">All trends</option>
            <option value="strong">Strong</option>
            <option value="moderate">Moderate</option>
            <option value="weak">Weak</option>
          </select>
        </label>

        <label className="flex flex-col gap-1.5 text-xs text-text-secondary">
          Signal quality
          <select className={CONTROL_CLASS} value={quality} onChange={(event) => setQuality(event.target.value as QualityFilter)}>
            <option value="all">All signal quality</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
      </div>

      {displayed.length > 0 ? (
        <div className="mt-5 overflow-x-auto">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead className="border-b border-border text-xs text-text-secondary">
              <tr>
                <th className="pb-3 pr-4 font-medium">Stock</th>
                <th className="pb-3 pr-4 font-medium">Artha Score</th>
                <th className="pb-3 pr-4 font-medium">Rating</th>
                <th className="pb-3 pr-4 font-medium">Confidence</th>
                <th className="pb-3 pr-4 font-medium">Liquidity</th>
                <th className="pb-3 pr-4 font-medium">Trend</th>
                <th className="pb-3 pr-4 font-medium">Signal quality</th>
                <th className="pb-3 font-medium">Active evidence</th>
              </tr>
            </thead>
            <tbody>
              {displayed.map((stock) => (
                <tr key={stock.symbol} className="border-b border-border/70 last:border-0">
                  <td className="py-3 pr-4">
                    <Link href={`/stock/${stock.symbol}`} className="group block min-w-[180px]">
                      <span className="font-semibold text-text-primary group-hover:text-accent-text">{stock.symbol}</span>
                      <span className="mt-0.5 block max-w-[220px] truncate text-xs text-text-secondary">{stock.company_name}</span>
                    </Link>
                  </td>
                  <td className={`py-3 pr-4 font-semibold tabular-nums ${scoreTone(stock.artha_score)}`}>{stock.artha_score}</td>
                  <td className="py-3 pr-4 text-text-primary">{prettyLabel(stock.rating)}</td>
                  <td className="py-3 pr-4 text-text-primary">{prettyLabel(stock.confidence_level)}</td>
                  <td className="py-3 pr-4 text-text-primary">{prettyLabel(stock.liquidity_tier)}</td>
                  <td className="py-3 pr-4 text-text-primary">{prettyLabel(stock.trend_strength)}</td>
                  <td className="py-3 pr-4 text-text-primary">{prettyLabel(stock.signal_quality)}</td>
                  <td className="py-3 text-xs text-text-secondary">
                    {stock.active_signals.length > 0 ? stock.active_signals.slice(0, 2).map(prettyLabel).join(" · ") : "No active validated signal"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > displayed.length ? (
            <p className="mt-3 text-xs text-text-secondary">Showing the top {displayed.length} matches by Artha Score. Tighten filters to narrow the list.</p>
          ) : null}
        </div>
      ) : (
        <div className="mt-5 rounded-lg border border-border bg-background p-6 text-center">
          <p className="text-sm font-medium text-text-primary">No stocks match this filter set.</p>
          <p className="mt-1 text-xs text-text-secondary">Reduce the score threshold or broaden confidence, liquidity, trend or signal quality.</p>
        </div>
      )}
    </section>
  );
}
