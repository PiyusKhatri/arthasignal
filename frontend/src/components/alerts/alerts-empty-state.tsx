import { Bell } from "lucide-react";

export function AlertsEmptyState({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-card px-6 py-10 text-center">
      <Bell className="size-8 text-text-secondary" aria-hidden="true" />
      <p className="text-sm font-medium text-text-primary">{label}</p>
    </div>
  );
}
