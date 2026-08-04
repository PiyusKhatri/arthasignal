"use client";

import { Star } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

export function WatchlistToggleButton({
  symbol,
  initialWatching,
  isAuthenticated,
}: {
  symbol: string;
  initialWatching: boolean;
  isAuthenticated: boolean;
}) {
  const router = useRouter();
  const [watching, setWatching] = useState(initialWatching);
  const [pending, setPending] = useState(false);

  async function handleClick() {
    if (!isAuthenticated) {
      router.push(`/login?next=/stock/${symbol}`);
      return;
    }

    setPending(true);
    try {
      if (watching) {
        const response = await fetch(`/api/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" });
        if (response.ok) {
          setWatching(false);
        }
      } else {
        const response = await fetch("/api/watchlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol }),
        });
        if (response.ok) {
          setWatching(true);
        }
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={pending}
      aria-pressed={watching}
      className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-60 ${
        watching
          ? "border-accent-primary bg-accent-primary/10 text-accent-text"
          : "border-border text-text-secondary hover:text-text-primary"
      }`}
    >
      <Star className="size-4" aria-hidden="true" fill={watching ? "currentColor" : "none"} />
      {watching ? "Watching" : "Watchlist"}
    </button>
  );
}
