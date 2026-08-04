"use client";

import { useState } from "react";
import { WatchlistEmptyState } from "@/components/watchlist/watchlist-empty-state";
import { WatchlistTable } from "@/components/watchlist/watchlist-table";
import type { WatchlistItem } from "@/lib/watchlist-data";

export function WatchlistClient({ initialItems }: { initialItems: WatchlistItem[] }) {
  const [items, setItems] = useState(initialItems);

  async function refresh() {
    const response = await fetch("/api/watchlist", { cache: "no-store" });
    if (response.ok) {
      setItems((await response.json()) as WatchlistItem[]);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <section>
        <h1 className="text-lg font-semibold text-text-primary">Watchlist</h1>
        <div className="mt-4">
          {items.length > 0 ? <WatchlistTable items={items} onChanged={refresh} /> : <WatchlistEmptyState />}
        </div>
      </section>
    </div>
  );
}
