"use client";

import { useState, type FormEvent } from "react";
import { FormError } from "@/components/auth/form-error";
import { FormField } from "@/components/auth/form-field";
import { SubmitButton } from "@/components/auth/submit-button";
import { SymbolSearchInput } from "@/components/portfolio/symbol-search-input";
import { parseApiErrorMessage } from "@/lib/api-error";
import type { PriceAlertCondition } from "@/lib/alerts-data";
import { validatePositiveNumber, validateSymbol } from "@/lib/validation";

export function PriceAlertForm({ onSaved, onCancel }: { onSaved: () => void; onCancel: () => void }) {
  const [symbol, setSymbol] = useState("");
  const [condition, setCondition] = useState<PriceAlertCondition>("above");
  const [targetPrice, setTargetPrice] = useState("");
  const [symbolError, setSymbolError] = useState<string | null>(null);
  const [priceError, setPriceError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const nextSymbolError = validateSymbol(symbol);
    const nextPriceError = validatePositiveNumber(targetPrice, "Target price");
    setSymbolError(nextSymbolError);
    setPriceError(nextPriceError);
    if (nextSymbolError || nextPriceError) {
      return;
    }

    setPending(true);
    try {
      const response = await fetch("/api/alerts/price", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: symbol.trim().toUpperCase(),
          condition,
          target_price: targetPrice,
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        setFormError(parseApiErrorMessage(data, "Could not create price alert. Please try again."));
        return;
      }
      onSaved();
    } catch {
      setFormError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <form onSubmit={handleSubmit} noValidate>
        <FormError message={formError} />
        <SymbolSearchInput value={symbol} onSelect={setSymbol} error={symbolError} />
        <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          <div className="mb-4">
            <label htmlFor="price-alert-condition" className="mb-1 block text-sm text-text-secondary">
              Condition
            </label>
            <select
              id="price-alert-condition"
              value={condition}
              onChange={(event) => setCondition(event.target.value as PriceAlertCondition)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent-primary"
            >
              <option value="above">Price goes above</option>
              <option value="below">Price goes below</option>
            </select>
          </div>
          <FormField
            id="target-price"
            label="Target price (NPR)"
            type="number"
            value={targetPrice}
            onChange={setTargetPrice}
            error={priceError}
            min="0"
            step="any"
          />
        </div>
        <div className="flex items-center gap-3">
          <div className="w-40">
            <SubmitButton label="Create alert" pending={pending} />
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary transition-colors hover:text-text-primary"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
