import Link from "next/link";
import { Star } from "lucide-react";

export function WatchlistEmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-card px-6 py-12 text-center">
      <Star className="size-8 text-text-secondary" aria-hidden="true" />
      <p className="text-sm font-medium text-text-primary">No stocks watched yet</p>
      <p className="max-w-sm text-sm text-text-secondary">
        Open a stock&apos;s page and use the watchlist button to start tracking its price and signals here.
      </p>
      <Link
        href="/dashboard"
        className="mt-1 rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary-light"
      >
        Browse stocks
      </Link>
    </div>
  );
}
