import { Wallet } from "lucide-react";

export function PortfolioEmptyState({ onAddClick }: { onAddClick: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-card px-6 py-12 text-center">
      <Wallet className="size-8 text-text-secondary" aria-hidden="true" />
      <p className="text-sm font-medium text-text-primary">No holdings yet</p>
      <p className="max-w-sm text-sm text-text-secondary">
        Add your first stock holding to start tracking invested amount, current value, and unrealized P/L in one
        place.
      </p>
      <button
        type="button"
        onClick={onAddClick}
        className="mt-1 rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary-light"
      >
        Add your first holding
      </button>
    </div>
  );
}
