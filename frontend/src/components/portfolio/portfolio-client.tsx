"use client";

import { useState } from "react";
import { AddHoldingPanel } from "@/components/portfolio/add-holding-panel";
import { HoldingsTable } from "@/components/portfolio/holdings-table";
import { PortfolioEmptyState } from "@/components/portfolio/portfolio-empty-state";
import { PortfolioSummaryCards } from "@/components/portfolio/portfolio-summary-cards";
import type { PortfolioSummary } from "@/lib/portfolio-data";

export function PortfolioClient({ initialSummary }: { initialSummary: PortfolioSummary }) {
  const [summary, setSummary] = useState(initialSummary);
  const [showAddForm, setShowAddForm] = useState(false);

  async function refresh() {
    const response = await fetch("/api/portfolio/summary", { cache: "no-store" });
    if (response.ok) {
      setSummary((await response.json()) as PortfolioSummary);
    }
  }

  const hasHoldings = summary.holdings.length > 0;

  return (
    <div className="flex flex-col gap-8">
      <section>
        <h1 className="text-lg font-semibold text-text-primary">Portfolio</h1>
        <div className="mt-4">
          <PortfolioSummaryCards summary={summary} />
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text-primary">Holdings</h2>
          {hasHoldings ? (
            <button
              type="button"
              onClick={() => setShowAddForm((value) => !value)}
              className="rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary-light"
            >
              {showAddForm ? "Cancel" : "Add holding"}
            </button>
          ) : null}
        </div>

        {showAddForm ? (
          <div className="mt-4">
            <AddHoldingPanel
              onSaved={() => {
                setShowAddForm(false);
                refresh();
              }}
              onCancel={() => setShowAddForm(false)}
            />
          </div>
        ) : null}

        <div className="mt-4">
          {hasHoldings ? (
            <HoldingsTable holdings={summary.holdings} onChanged={refresh} />
          ) : !showAddForm ? (
            <PortfolioEmptyState onAddClick={() => setShowAddForm(true)} />
          ) : null}
        </div>
      </section>
    </div>
  );
}
