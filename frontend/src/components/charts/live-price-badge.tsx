"use client";

import { useEffect, useState } from "react";
import type { ChartTarget } from "@/components/charts/timeframe-chart";

const POLL_INTERVAL_MS = 60000;

type IntradayPoint = { time: number; price: string };
type IntradayResponse = { has_data: boolean; points: IntradayPoint[] };

function formatPrice(value: string): string {
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function intradayUrl(target: ChartTarget): string {
  if (target.kind === "stock") {
    return `/api/stocks/${encodeURIComponent(target.symbol)}/intraday-today`;
  }
  return `/api/market/index-intraday-today`;
}

export function LivePriceBadge({ target }: { target: ChartTarget }) {
  const [point, setPoint] = useState<IntradayPoint | null>(null);
  const targetKey = target.kind === "stock" ? target.symbol : "index";

  useEffect(() => {
    let ignore = false;

    const poll = () => {
      fetch(intradayUrl(target), { cache: "no-store" })
        .then((response) => (response.ok ? response.json() : null))
        .then((data: IntradayResponse | null) => {
          if (ignore) {
            return;
          }
          if (data && data.has_data && data.points.length > 0) {
            setPoint(data.points[data.points.length - 1]);
          } else {
            setPoint(null);
          }
        })
        .catch(() => {
          if (!ignore) {
            setPoint(null);
          }
        });
    };

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      ignore = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey]);

  if (!point) {
    return null;
  }

  const prefix = target.kind === "stock" ? "Rs " : "";

  return (
    <span className="flex items-center gap-1.5 text-xs font-medium text-success-text" data-testid="live-price-badge">
      <span className="inline-block size-1.5 rounded-full bg-success-text" aria-hidden="true" />
      Live {prefix}
      {formatPrice(point.price)}
    </span>
  );
}
