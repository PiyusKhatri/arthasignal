"use client";

import { useState } from "react";
import Link from "next/link";
import { Pencil, Trash2, X, Check } from "lucide-react";
import { parseApiErrorMessage } from "@/lib/api-error";
import type { HoldingSummary } from "@/lib/portfolio-data";
import { validateDate, validatePositiveNumber } from "@/lib/validation";

function formatNumber(value: string, decimals = 2): string {
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function formatSignedPercent(value: string | null): string {
  if (value === null) {
    return "—";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

function EditRow({
  holding,
  onCancel,
  onSaved,
}: {
  holding: HoldingSummary;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [quantity, setQuantity] = useState(holding.quantity);
  const [purchasePrice, setPurchasePrice] = useState(holding.purchase_price);
  const [purchaseDate, setPurchaseDate] = useState(holding.purchase_date);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSave() {
    const quantityError = validatePositiveNumber(quantity, "Quantity");
    const priceError = validatePositiveNumber(purchasePrice, "Purchase price");
    const dateError = validateDate(purchaseDate);
    const firstError = quantityError ?? priceError ?? dateError;
    if (firstError) {
      setError(firstError);
      return;
    }

    setPending(true);
    setError(null);
    try {
      const response = await fetch(`/api/portfolio/holdings/${holding.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quantity, purchase_price: purchasePrice, purchase_date: purchaseDate }),
      });
      const data = await response.json();
      if (!response.ok) {
        setError(parseApiErrorMessage(data, "Could not update holding."));
        return;
      }
      onSaved();
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  }

  const inputClass =
    "w-full rounded-md border border-border bg-background px-2 py-1 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent-primary";

  return (
    <tr>
      <td className="px-4 py-3">
        <p className="font-medium text-text-primary">{holding.symbol}</p>
      </td>
      <td className="px-4 py-3">
        <input
          type="number"
          min="0"
          step="any"
          value={quantity}
          onChange={(event) => setQuantity(event.target.value)}
          className={inputClass}
          aria-label="Quantity"
        />
      </td>
      <td className="px-4 py-3">
        <input
          type="number"
          min="0"
          step="any"
          value={purchasePrice}
          onChange={(event) => setPurchasePrice(event.target.value)}
          className={inputClass}
          aria-label="Purchase price"
        />
      </td>
      <td className="px-4 py-3">
        <input
          type="date"
          value={purchaseDate}
          max={new Date().toISOString().slice(0, 10)}
          onChange={(event) => setPurchaseDate(event.target.value)}
          className={inputClass}
          aria-label="Purchase date"
        />
      </td>
      <td className="px-4 py-3 text-text-secondary">—</td>
      <td className="px-4 py-3 text-text-secondary">—</td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={pending}
            aria-label="Save"
            className="rounded-md p-1.5 text-success-text hover:bg-success/10 disabled:opacity-60"
          >
            <Check className="size-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={onCancel}
            aria-label="Cancel"
            className="rounded-md p-1.5 text-text-secondary hover:bg-background"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>
        {error ? <p className="mt-1 text-xs text-danger-text">{error}</p> : null}
      </td>
    </tr>
  );
}

export function HoldingsTable({ holdings, onChanged }: { holdings: HoldingSummary[]; onChanged: () => void }) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  async function handleDelete(id: number) {
    setDeletingId(id);
    try {
      const response = await fetch(`/api/portfolio/holdings/${id}`, { method: "DELETE" });
      if (response.ok) {
        onChanged();
      }
    } finally {
      setDeletingId(null);
      setConfirmDeleteId(null);
    }
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-secondary">
            <th className="px-4 py-3 font-medium">Symbol</th>
            <th className="px-4 py-3 font-medium">Quantity</th>
            <th className="px-4 py-3 font-medium">Purchase price</th>
            <th className="px-4 py-3 font-medium">Purchase date</th>
            <th className="px-4 py-3 font-medium">Current price</th>
            <th className="px-4 py-3 font-medium">P/L</th>
            <th className="px-4 py-3 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {holdings.map((holding) => {
            if (editingId === holding.id) {
              return (
                <EditRow
                  key={holding.id}
                  holding={holding}
                  onCancel={() => setEditingId(null)}
                  onSaved={() => {
                    setEditingId(null);
                    onChanged();
                  }}
                />
              );
            }

            const plNum = holding.unrealized_pl !== null ? Number(holding.unrealized_pl) : null;
            const isUp = plNum !== null && plNum >= 0;

            return (
              <tr key={holding.id}>
                <td className="px-4 py-3">
                  <Link href={`/stock/${holding.symbol}`} className="font-medium text-text-primary hover:underline">
                    {holding.symbol}
                  </Link>
                </td>
                <td className="px-4 py-3 text-text-primary">{formatNumber(holding.quantity, 0)}</td>
                <td className="px-4 py-3 text-text-primary">{formatNumber(holding.purchase_price)}</td>
                <td className="px-4 py-3 text-text-secondary">{holding.purchase_date}</td>
                <td className="px-4 py-3 text-text-primary">
                  {holding.current_price !== null ? formatNumber(holding.current_price) : "Unavailable"}
                </td>
                <td className="px-4 py-3">
                  {plNum !== null ? (
                    <span className={`font-medium ${isUp ? "text-success-text" : "text-danger-text"}`}>
                      {isUp ? "+" : ""}
                      {formatNumber(holding.unrealized_pl as string)} ({formatSignedPercent(holding.unrealized_pl_percent)})
                    </span>
                  ) : (
                    <span className="text-text-secondary">Unavailable</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {confirmDeleteId === holding.id ? (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-text-secondary">Delete?</span>
                      <button
                        type="button"
                        onClick={() => handleDelete(holding.id)}
                        disabled={deletingId === holding.id}
                        className="rounded-md px-2 py-1 text-xs font-medium text-danger-text hover:bg-danger/10 disabled:opacity-60"
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDeleteId(null)}
                        className="rounded-md px-2 py-1 text-xs font-medium text-text-secondary hover:bg-background"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setEditingId(holding.id)}
                        aria-label={`Edit ${holding.symbol}`}
                        className="rounded-md p-1.5 text-text-secondary hover:bg-background hover:text-text-primary"
                      >
                        <Pencil className="size-4" aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDeleteId(holding.id)}
                        aria-label={`Delete ${holding.symbol}`}
                        className="rounded-md p-1.5 text-text-secondary hover:bg-danger/10 hover:text-danger-text"
                      >
                        <Trash2 className="size-4" aria-hidden="true" />
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
