import { redirect } from "next/navigation";
import { WatchlistClient } from "@/components/watchlist/watchlist-client";
import { getWatchlist } from "@/lib/watchlist-data";

export default async function WatchlistPage() {
  const { items, unauthorized } = await getWatchlist();

  if (unauthorized || !items) {
    redirect("/login?next=/watchlist");
  }

  return <WatchlistClient initialItems={items} />;
}
