"use client";

import { ChevronDown, TrendingDown, TrendingUp } from "lucide-react";
import { Fragment, useState } from "react";
import { SectorStocksPanel } from "@/components/sectors/sector-stocks-panel";
import type { SectorPerformance } from "@/lib/market-data";

function formatSignedPercent(value: string | null, digits = 2): string {
  if (value === null) {
    return "No data";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(digits)}%`;
}

function formatRatio(value: string | null): string {
  if (value === null) {
    return "No data";
  }
  return `${Number(value).toFixed(2)}x avg`;
}

function brokerConcentrationSummary(sector: SectorPerformance): { label: string; muted: boolean } {
  const bc = sector.broker_concentration;
  if (!bc.available) {
    return { label: "No floorsheet data today", muted: true };
  }
  if (!bc.coverage_reliable) {
    return { label: "Coverage too thin to trust", muted: true };
  }
  const combined = bc.top_brokers.reduce((sum, broker) => sum + Number(broker.fraction_of_sector_turnover ?? 0), 0);
  return { label: `Top ${bc.top_brokers.length} brokers: ${(combined * 100).toFixed(0)}% of turnover`, muted: false };
}

function ChangeBar({ value, maxAbs }: { value: number; maxAbs: number }) {
  const widthPercent = maxAbs > 0 ? Math.min(100, (Math.abs(value) / maxAbs) * 100) : 0;
  const isUp = value >= 0;
  return (
    <div className="h-1.5 w-24 overflow-hidden rounded-full bg-border">
      <div
        className={`h-full rounded-full ${isUp ? "bg-success-text" : "bg-danger-text"}`}
        style={{ width: `${widthPercent}%` }}
      />
    </div>
  );
}

export function SectorRankTable({ sectors }: { sectors: SectorPerformance[] }) {
  const [expandedSector, setExpandedSector] = useState<string | null>(null);

  if (sectors.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-sm text-text-secondary">
        Sector performance data is temporarily unavailable.
      </div>
    );
  }

  const maxAbsChange = Math.max(
    ...sectors.map((sector) =>
      sector.market_cap_weighted_percent_change !== null ? Math.abs(Number(sector.market_cap_weighted_percent_change)) : 0
    ),
    0.01
  );

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-secondary">
            <th className="px-4 py-3 font-medium">Rank</th>
            <th className="px-4 py-3 font-medium">Sector</th>
            <th className="px-4 py-3 font-medium">Mkt-cap weighted change</th>
            <th className="px-4 py-3 font-medium">Advance / decline</th>
            <th className="px-4 py-3 font-medium">Turnover vs 20d avg</th>
            <th className="px-4 py-3 font-medium">Broker concentration</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {sectors.map((sector, index) => {
            const change = sector.market_cap_weighted_percent_change;
            const changeNum = change !== null ? Number(change) : null;
            const isUp = changeNum !== null && changeNum >= 0;
            const brokerSummary = brokerConcentrationSummary(sector);
            const { advances, declines, unchanged } = sector.advance_decline;
            const isExpanded = expandedSector === sector.sector;
            const toggleExpanded = () => setExpandedSector(isExpanded ? null : sector.sector);

            return (
              <Fragment key={sector.sector}>
                <tr
                  onClick={toggleExpanded}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      toggleExpanded();
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  aria-expanded={isExpanded}
                  aria-label={`${sector.sector} constituent stocks`}
                  className="cursor-pointer hover:bg-background/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-primary-light"
                >
                  <td className="px-4 py-3 align-top">
                    <span className="inline-flex size-6 items-center justify-center rounded-md bg-border text-xs font-medium text-text-secondary">
                      {index + 1}
                    </span>
                  </td>
                  <td className="px-4 py-3 align-top font-medium text-text-primary">
                    <span className="flex items-center gap-1.5">
                      <ChevronDown
                        className={`size-3.5 text-text-secondary transition-transform ${isExpanded ? "rotate-180" : ""}`}
                        aria-hidden="true"
                      />
                      {sector.sector}
                    </span>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center gap-2">
                      <span
                        className={`flex items-center gap-1 font-medium ${
                          changeNum === null ? "text-text-secondary" : isUp ? "text-success-text" : "text-danger-text"
                        }`}
                      >
                        {changeNum !== null ? (
                          isUp ? (
                            <TrendingUp className="size-3.5" aria-hidden="true" />
                          ) : (
                            <TrendingDown className="size-3.5" aria-hidden="true" />
                          )
                        ) : null}
                        {formatSignedPercent(change)}
                      </span>
                      {changeNum !== null ? <ChangeBar value={changeNum} maxAbs={maxAbsChange} /> : null}
                    </div>
                  </td>
                  <td className="px-4 py-3 align-top text-text-secondary">
                    <span className="text-success-text">{advances}↑</span> · <span className="text-danger-text">{declines}↓</span> ·{" "}
                    {unchanged} flat
                  </td>
                  <td className="px-4 py-3 align-top text-text-secondary">
                    {formatRatio(sector.turnover_trend.turnover_vs_trailing_avg_ratio)}
                  </td>
                  <td className={`px-4 py-3 align-top ${brokerSummary.muted ? "text-text-secondary" : "text-text-primary"}`}>
                    {brokerSummary.label}
                  </td>
                </tr>
                {isExpanded ? (
                  <tr className="bg-background/40">
                    <td colSpan={6} className="p-0">
                      <SectorStocksPanel sector={sector.sector} />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
