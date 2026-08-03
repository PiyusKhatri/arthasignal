import type { ReactNode } from "react";

export function StatCard({ label, value, meta }: { label: string; value: ReactNode; meta?: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <p className="text-xs text-text-secondary">{label}</p>
      <div className="mt-2 text-xl font-semibold text-text-primary">{value}</div>
      {meta ? <div className="mt-1 text-xs text-text-secondary">{meta}</div> : null}
    </div>
  );
}
