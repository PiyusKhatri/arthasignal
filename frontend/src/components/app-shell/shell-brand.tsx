import Link from "next/link";
import { Activity } from "lucide-react";

export function ShellBrand({ compact = false, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  return (
    <Link
      href="/dashboard"
      onClick={onNavigate}
      aria-label="ArthaSignal dashboard"
      className={`group flex min-w-0 items-center ${compact ? "justify-center" : "gap-3"}`}
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-accent-primary text-white shadow-sm">
        <Activity className="size-5" aria-hidden="true" />
      </span>
      {!compact ? (
        <span className="min-w-0 leading-tight">
          <span className="block truncate text-sm font-semibold tracking-tight text-text-primary">ArthaSignal</span>
          <span className="mt-0.5 block truncate text-[10px] font-medium uppercase tracking-[0.14em] text-text-secondary">
            NEPSE intelligence
          </span>
        </span>
      ) : null}
    </Link>
  );
}
