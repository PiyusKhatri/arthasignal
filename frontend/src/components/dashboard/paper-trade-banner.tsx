import Link from "next/link";

export function PaperTradeBanner() {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-card px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <p className="text-text-secondary">Every signal shown here is in live paper-trade validation, not a guarantee.</p>
      <Link href="/transparency" className="shrink-0 text-accent-text hover:text-text-primary">
        See the track record
      </Link>
    </div>
  );
}
