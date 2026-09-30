"use client";

import { useState } from "react";
import Link from "next/link";
import { Trash2 } from "lucide-react";
import type { SignalAlert } from "@/lib/alerts-data";

export function SignalAlertsList({ alerts, onChanged }: { alerts: SignalAlert[]; onChanged: () => void }) {
  const [deletingId, setDeletingId] = useState<number | null>(null);

  async function handleDelete(id: number) {
    setDeletingId(id);
    try {
      const response = await fetch(`/api/alerts/signal/${id}`, { method: "DELETE" });
      if (response.ok) {
        onChanged();
      }
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <ul className="divide-y divide-border rounded-lg border border-border bg-card">
      {alerts.map((alert) => (
        <li key={alert.id} className="flex items-center justify-between gap-4 px-4 py-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-medium text-text-primary">{alert.signal_name}</span>
              {alert.triggered_at ? (
                <span className="rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success-text">
                  Triggered
                </span>
              ) : (
                <span className="rounded-full bg-background px-2 py-0.5 text-xs font-medium text-text-secondary">
                  Active
                </span>
              )}
            </div>
            <p className="mt-0.5 text-sm text-text-secondary">
              {alert.symbol ? (
                <>
                  On{" "}
                  <Link href={`/stock/${alert.symbol}`} className="hover:underline">
                    {alert.symbol}
                  </Link>
                </>
              ) : (
                "Any symbol"
              )}
              {alert.triggered_symbol ? (
                <>
                  {" "}
                  · fired on{" "}
                  <Link href={`/stock/${alert.triggered_symbol}`} className="hover:underline">
                    {alert.triggered_symbol}
                  </Link>
                </>
              ) : null}
              {alert.triggered_at ? ` · triggered ${new Date(alert.triggered_at).toLocaleString()}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={() => handleDelete(alert.id)}
            disabled={deletingId === alert.id}
            aria-label={`Delete alert for ${alert.signal_name}`}
            className="rounded-md p-1.5 text-text-secondary hover:bg-danger/10 hover:text-danger-text disabled:opacity-60"
          >
            <Trash2 className="size-4" aria-hidden="true" />
          </button>
        </li>
      ))}
    </ul>
  );
}
